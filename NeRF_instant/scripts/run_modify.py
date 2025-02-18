#!/usr/bin/env python3

# Copyright (c) 2020-2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

import argparse
import os
import commentjson as json

import glm

import numpy as np

import shutil
import time

from common import *
from scenes import *

from tqdm import tqdm

import pyngp as ngp # noqa

from sksurgerynditracker.nditracker import NDITracker
import pickle
from scipy.spatial.transform import Rotation as R

import keyboard  # using module keyboard
import csv

import time

def flip_matrix(m):
	c2w = np.linalg.inv(m)
	c2w[0:3,2] *= -1 # flip the y and z axis
	c2w[0:3,1] *= -1
	c2w = c2w[[1,0,2,3],:]
	c2w[2,:] *= -1 # flip whole world upside down
	return c2w

def rotation_matrix_to_vector(matrix):
    old_vec = matrix[:, 2]
    vec = [-old_vec[1], -old_vec[2], -old_vec[0]]
    return vec

def rotation_matrix_z(angle_deg):
    angle_rad = np.deg2rad(angle_deg)

    cos_theta = np.cos(angle_rad)
    sin_theta = np.sin(angle_rad)

    return np.array([
        [cos_theta, -sin_theta, 0],
        [sin_theta, cos_theta, 0],
        [0, 0, 1]
    ])
def rotation_matrix_x(angle_deg):
    angle_rad = np.deg2rad(angle_deg)

    cos_theta = np.cos(angle_rad)
    sin_theta = np.sin(angle_rad)

    return np.array([
        [1, 0, 0],
        [0, cos_theta, -sin_theta],
        [0, sin_theta, cos_theta]
    ])

def rotation_matrix_y(angle_deg):
    angle_rad = np.deg2rad(angle_deg)

    cos_theta = np.cos(angle_rad)
    sin_theta = np.sin(angle_rad)

    return np.array([
        [cos_theta, 0, sin_theta],
        [0, 1, 0],
        [-sin_theta, 0, cos_theta]
    ])

def calculate_relative_positions(base_pos, stream):
    relative_stream = []
    for i, data in stream:
        # Assuming the last column of both base_pos and data arrays hold the relevant 3D positions
        base_column = base_pos[:3, -1]
        data_column = data[:3, -1]
        
        # Subtract the two columns to get the relative position
        relative_data = data_column - base_column
        relative_stream.append([i, list(relative_data)])
    return relative_stream

def parse_args():
	parser = argparse.ArgumentParser(description="Run instant neural graphics primitives with additional configuration & output options")

	parser.add_argument("files", nargs="*", help="Files to be loaded. Can be a scene, network config, snapshot, camera path, or a combination of those.")

	parser.add_argument("--scene", "--training_data", default="", help="The scene to load. Can be the scene's name or a full path to the training data. Can be NeRF dataset, a *.obj/*.stl mesh for training a SDF, an image, or a *.nvdb volume.")
	parser.add_argument("--mode", default="", type=str, help=argparse.SUPPRESS) # deprecated
	parser.add_argument("--network", default="", help="Path to the network config. Uses the scene's default if unspecified.")

	parser.add_argument("--load_snapshot", "--snapshot", default="", help="Load this snapshot before training. recommended extension: .ingp/.msgpack")
	parser.add_argument("--save_snapshot", default="", help="Save this snapshot after training. recommended extension: .ingp/.msgpack")

	parser.add_argument("--nerf_compatibility", action="store_true", help="Matches parameters with original NeRF. Can cause slowness and worse results on some scenes, but helps with high PSNR on synthetic scenes.")
	#################
	parser.add_argument("--test_transforms", default="", help="Path to a nerf style transforms json from which we will compute PSNR.")
	parser.add_argument("--near_distance", default=-1, type=float, help="Set the distance from the camera at which training rays start for nerf. <0 means use ngp default")
	parser.add_argument("--exposure", default=0.0, type=float, help="Controls the brightness of the image. Positive numbers increase brightness, negative numbers decrease it.")
    
	##################
	parser.add_argument("--screenshot_transforms", default="", help="Path to a nerf style transforms.json from which to save screenshots.")
	parser.add_argument("--screenshot_frames", nargs="*", help="Which frame(s) to take screenshots of.")
	##################
	parser.add_argument("--screenshot_dir", default="", help="Which directory to output screenshots to.")
	parser.add_argument("--screenshot_spp", type=int, default=16, help="Number of samples per pixel in screenshots.")

	parser.add_argument("--video_camera_path", default="", help="The camera path to render, e.g., base_cam.json.")
	parser.add_argument("--video_camera_smoothing", action="store_true", help="Applies additional smoothing to the camera trajectory with the caveat that the endpoint of the camera path may not be reached.")
	parser.add_argument("--video_fps", type=int, default=60, help="Number of frames per second.")
	parser.add_argument("--video_n_seconds", type=int, default=1, help="Number of seconds the rendered video should be long.")
	parser.add_argument("--video_render_range", type=int, nargs=2, default=(-1, -1), metavar=("START_FRAME", "END_FRAME"), help="Limit output to frames between START_FRAME and END_FRAME (inclusive)")
	parser.add_argument("--video_spp", type=int, default=8, help="Number of samples per pixel. A larger number means less noise, but slower rendering.")
	parser.add_argument("--video_output", type=str, default="video.mp4", help="Filename of the output video (video.mp4) or video frames (video_%%04d.png).")

	parser.add_argument("--save_mesh", default="", help="Output a marching-cubes based mesh from the NeRF or SDF model. Supports OBJ and PLY format.")
	parser.add_argument("--marching_cubes_res", default=256, type=int, help="Sets the resolution for the marching cubes grid.")

	parser.add_argument("--width", "--screenshot_w", type=int, default=0, help="Resolution width of GUI and screenshots.")
	parser.add_argument("--height", "--screenshot_h", type=int, default=0, help="Resolution height of GUI and screenshots.")

	parser.add_argument("--gui", action="store_true", help="Run the testbed GUI interactively.")
	parser.add_argument("--train", action="store_true", help="If the GUI is enabled, controls whether training starts immediately.")
	parser.add_argument("--n_steps", type=int, default=-1, help="Number of steps to train for before quitting.")
	parser.add_argument("--second_window", action="store_true", help="Open a second window containing a copy of the main output.")
	parser.add_argument("--vr", action="store_true", help="Render to a VR headset.")

	parser.add_argument("--sharpen", default=0, help="Set amount of sharpening applied to NeRF training images. Range 0.0 to 1.0.")

	# added parameters
	parser.add_argument("--ndi",action="store_true", help="Whether use NDI for view generation")
	parser.add_argument("--row", default="c:/Users/camp/Documents/Xinrui Zou/Tool-ROM/no-pivot.rom", help="used only when there is a ndi")
	# parser.add_argument("--row", default="c:/Users/camp/Documents/Xinrui Zou/Tool-ROM/plexy_pointer.rom", help="used only when there is a ndi")
	# parser.add_argument("--calibration", default="C:/Users/camp/GIT/arthro_nerf/camera_calibration/handeye_matrix_new_2.csv")
	parser.add_argument("--calibration", default="C:/Users/camp/GIT/arthro_nerf/handeye-v2/handeye_matrix.csv")
	parser.add_argument("--world_center", default="C:/Users/camp/GIT/arthro_nerf/Newplatformar/world_center.pkl")
	# parser.add_argument("--post_R", default="C:/Users/camp/GIT/arthro_nerf/LEGO/postprocess-R.pkl")
	parser.add_argument("--post_info", default="C:/Users/camp/GIT/arthro_nerf/Newplatformar/postprocess-info.pkl")
	parser.add_argument("--evaluation_list", default="C:/Users/camp/GIT/arthro_nerf/Newplatformar/evaluation.csv")

	# Evaluation
	parser.add_argument("--evaluation",action="store_true", help="Whether to do the evaluation with baseboard")
	parser.add_argument("--base_pose",  default="C:/Users/camp/GIT/arthro_nerf/utils/evaluations/base_pose.pickle", help="used only when there is a ndi")
	parser.add_argument("--user_id", type=float, default=0, help="get the user number")
	return parser.parse_args()

def get_scene(scene):
	for scenes in [scenes_sdf, scenes_nerf, scenes_image, scenes_volume]:
		if scene in scenes:
			return scenes[scene]
	return None

if __name__ == "__main__":
	args = parse_args()
	if args.vr: # VR implies having the GUI running at the moment
		args.gui = True

	if args.mode:
		print("Warning: the '--mode' argument is no longer in use. It has no effect. The mode is automatically chosen based on the scene.")

	testbed = ngp.Testbed()
	testbed.root_dir = ROOT_DIR

	for file in args.files:
		scene_info = get_scene(file)
		if scene_info:
			file = os.path.join(scene_info["data_dir"], scene_info["dataset"])
		testbed.load_file(file)

	if args.scene:
		scene_info = get_scene(args.scene)
		if scene_info is not None:
			args.scene = os.path.join(scene_info["data_dir"], scene_info["dataset"])
			if not args.network and "network" in scene_info:
				args.network = scene_info["network"]

		testbed.load_training_data(args.scene)

	if args.gui:
		# Pick a sensible GUI resolution depending on arguments.
		sw = args.width or 1920
		sh = args.height or 1080
		while sw * sh > 1920 * 1080 * 4:
			sw = int(sw / 2)
			sh = int(sh / 2)
		testbed.init_window(sw, sh, second_window=args.second_window)
		if args.vr:
			testbed.init_vr()


	if args.load_snapshot:
		scene_info = get_scene(args.load_snapshot)
		if scene_info is not None:
			args.load_snapshot = default_snapshot_filename(scene_info)
		testbed.load_snapshot(args.load_snapshot)
	elif args.network:
		testbed.reload_network_from_file(args.network)

	ref_transforms = {}
	if args.screenshot_transforms: # try to load the given file straight away
		print("Screenshot transforms from ", args.screenshot_transforms)
		with open(args.screenshot_transforms) as f:
			ref_transforms = json.load(f)

	if testbed.mode == ngp.TestbedMode.Sdf:
		testbed.tonemap_curve = ngp.TonemapCurve.ACES

	testbed.nerf.sharpen = float(args.sharpen)
	testbed.exposure = args.exposure
	testbed.shall_train = args.train if args.gui else True


	testbed.nerf.render_with_lens_distortion = True

	network_stem = os.path.splitext(os.path.basename(args.network))[0] if args.network else "base"
	if testbed.mode == ngp.TestbedMode.Sdf:
		setup_colored_sdf(testbed, args.scene)

	if args.near_distance >= 0.0:
		print("NeRF training ray near_distance ", args.near_distance)
		testbed.nerf.training.near_distance = args.near_distance

	if args.nerf_compatibility:
		print(f"NeRF compatibility mode enabled")

		# Prior nerf papers accumulate/blend in the sRGB
		# color space. This messes not only with background
		# alpha, but also with DOF effects and the likes.
		# We support this behavior, but we only enable it
		# for the case of synthetic nerf data where we need
		# to compare PSNR numbers to results of prior work.
		testbed.color_space = ngp.ColorSpace.SRGB

		# No exponential cone tracing. Slightly increases
		# quality at the cost of speed. This is done by
		# default on scenes with AABB 1 (like the synthetic
		# ones), but not on larger scenes. So force the
		# setting here.
		testbed.nerf.cone_angle_constant = 0

		# Match nerf paper behaviour and train on a fixed bg.
		testbed.nerf.training.random_bg_color = False

	old_training_step = 0
	n_steps = args.n_steps

	# If we loaded a snapshot, didn't specify a number of steps, _and_ didn't open a GUI,
	# don't train by default and instead assume that the goal is to render screenshots,
	# compute PSNR, or render a video.
	if n_steps < 0 and (not args.load_snapshot or args.gui):
		n_steps = 35000

	if args.gui and args.ndi:
		ROM_PATH = args.row
		SETTINGS = {
			"tracker type": "polaris",
			"romfiles": [ROM_PATH]
		}
		TRACKER = NDITracker(SETTINGS)
		TRACKER.start_tracking()

	
	tqdm_last_update = 0
	test_list = [[0,0,0],[0.2,0.2,0.2],[0.5,0.5,0.5]]
	tool2camera = np.loadtxt(args.calibration, dtype=float, delimiter=',')
	
	camera2tool = np.linalg.inv(tool2camera)
	with open(args.world_center, 'rb') as f:
		world_center = pickle.load(f)

	# with open(args.post_R, 'rb') as f:
	# 	post_R = pickle.load(f)

       
	with open(args.post_info, 'rb') as f:
		post_avglen = pickle.load(f)

	i = 0

	base_pose = None
	if args.evaluation:
		with open(args.base_pose, 'rb') as handle:
			base_pose = pickle.load(handle)
		
	testbed.usernum = args.user_id
	# Define a list to store the relative position 
	stream_list = []
	stream = []
	j = 0
	last_pressed_time = 0
	key_held = False
	pressnum_initialized = False

	prev_timer_value = 0
	start_time = None
	press_count = 0
	times_recorded = []
	time_duration = 0

	if n_steps > 0:
		with tqdm(desc="Training", total=n_steps, unit="steps") as t:
			while testbed.frame():
				if not pressnum_initialized:
					testbed.pressnum = [0,0,0]
					pressnum_initialized = True
				# TODO: ndi controller
				if args.gui and args.ndi:
					port_handles, timestamps, framenumbers, tracking, quality = TRACKER.get_frame()

					if not np.isnan(tracking[0][0][-1]):
						transform_matrix = tracking[0]
						# print("T1", transform_matrix)

						# ####### 1. handeye calibration
						transform_matrix = camera2tool @ np.linalg.inv(transform_matrix)
						# print("T2", transform_matrix)
						# ####### 2. use world center as origin ############
						# print("tool2camera", tool2camera)
						transform_matrix = transform_matrix @ world_center @ tool2camera
						# print("T3", transform_matrix)

						# # 1,2 Get right matix direction.

						# transform_matrix = transform_matrix @ world_center
						# print("T2", transform_matrix)
						# ####### 3. flip matrix (from opencv to opengl and from w2c to c2w)
						transform_matrix = flip_matrix(transform_matrix)
						# # # ####### 4. add ....
						# transform_matrix = np.matmul(post_R, transform_matrix)
                        # # # # ####### 5.
						# transform_matrix[0:3, 3] -= post_totp
                        # # # ####### 6.
						avg_post_avglen = sum(post_avglen) / len(post_avglen)
						# print("T4", transform_matrix)
						transform_matrix[0:3, 3] *= 4.0 / avg_post_avglen  # scale to "nerf sized"
						#transform_matrix = transform_matrix.tolist()
						# print(transform_matrix)
						tran_vec = transform_matrix[[0,1,2],-1]
						center = [0.5,-0.50478,0]

						
						
						# if keyboard.is_pressed('k'):
						


						if args.evaluation:
							# Detect a change in testbed.timer value
							if testbed.timer != prev_timer_value:
								if start_time is not None:  # Check if there's already a time being recorded
									times_recorded.append(time.time() - start_time)  # Store the duration for the previous timer value
								start_time = time.time()  # Start a new recording
								prev_timer_value = testbed.timer  # Update the previous timer value

							if keyboard.is_pressed('l'):
								testbed.pressnum = [0,0,0]  # Reset pressnum to [0,0,0]
								j = 0  # Reset any related counters
								testbed.pressnum = [j,0,0]

							if keyboard.is_pressed(' '):
								if keyboard.is_pressed(' ') and (time.time() - last_pressed_time >= 0.5):
									if not key_held:
										x = np.array([
										[1.79233051e-01, 7.81816542e-02, 9.80695234e-01, 2.69621530e+02],
										[2.05724419e-01, -9.77777731e-01, 4.03506246e-02, -2.86812080e+00],
										[9.62056639e-01, 1.94520791e-01, -1.91333961e-01, -6.97097727e+01],
										[0.00000000e+00, 0.00000000e+00, 0.00000000e+00, 1.00000000e+00]
										])
										transformed_data = tracking[0] @ x
										stream = [[j, transformed_data]]
										print('stream', stream)
										
										relative_stream = calculate_relative_positions(np.array(base_pose), stream)  # Converted base_pos to a NumPy array
										print(f"Relative stream: {relative_stream}")
										stream_list.append(relative_stream)
										print('Updated stream_list:', stream_list)  # Print the updated stream_list
										j = j+1
										press_count += 1
										# Check if space has been pressed 10 times
										if press_count % 10 == 0:
											if start_time is not None:
												times_recorded.append(time.time() - start_time)  # Store the duration
												start_time = None  # Reset start_time

										testbed.pressnum = [j,0,0]
										key_held = True
										last_pressed_time = time.time()
									else:
										key_held = False
						time_duration = 0 if start_time is None else time.time() - start_time
						testbed.tasktime = time_duration
						#print("time go: ", time_duration)
						print("time: ", times_recorded)
						testbed.look_at = [center[0] + tran_vec[1]/289.13, 0.5 + tran_vec[2]*0.0036754 , 0.5 + tran_vec[0]/313.7255]
						# print("camera_look_at: ", testbed.camera_matrix)
						# Extract translation
						translation = transform_matrix[:3,3]
					
						# Set view rotation with ? axis and 30 degrees
						rot_matrix_x = rotation_matrix_x(45)
						rot_matrix_y = rotation_matrix_y(45)
						rot_matrix_z = rotation_matrix_z(45)
						# The rotation matrix part
						rotation_matrix = transform_matrix[:3,:3]
						# Change the view if observation
						rotation_matrix = rotation_matrix @ rot_matrix_x
						vector = rotation_matrix_to_vector(rotation_matrix)
						testbed.view_dir = vector
						# print("camera_view_dir: ", testbed.camera_matrix)

						#Get up_dir
						vector_up = [rotation_matrix[1, 1], rotation_matrix[2, 1],rotation_matrix[0, 1]]
						testbed.up_dir = vector_up

						testbed.scale = 1.025
						k = testbed.camera_matrix
						# print("Transfer: ", k)
						k4 = k[:, -1]
						k1 = k[:, 0]
						k2 = k[:, 1]
						k3 = k[:, 2]
						testbed.ndi_rotation = k4
						testbed.ndi_camera_1 = k1
						testbed.ndi_camera_2 = k2
						testbed.ndi_camera_3 = k3
						# print("First Person View")
						testbed.look_at = [center[0] + tran_vec[1]/289.13, 0.5 + tran_vec[2]*0.0036754 , 0.5 + tran_vec[0]/313.7255]
						# print("camera_look_at1: ", testbed.camera_matrix)
						# Extract translation
						translation = transform_matrix[:3,3]
						# The rotation matrix part
						rotation_matrix = transform_matrix[:3,:3]
						# print("R1", rotation_matrix)
						vector = rotation_matrix_to_vector(rotation_matrix)
						testbed.view_dir = vector
						# print("camera_view_dir1: ", testbed.camera_matrix)
						# print("Final: ", testbed.camera_matrix)
						# print("Origin vector:", vector)
						#Get up_dir
						vector_up = [rotation_matrix[1, 1], rotation_matrix[2, 1],rotation_matrix[0, 1]]
						testbed.up_dir = vector_up
						testbed.scale = tran_vec[2]*0.0036754*0.4
						testbed.reset_accumulation()
							

						# else:
						# 		print("Third Person View")

						# 		#Set offset
						# 		testbed.look_at = [center[0] + tran_vec[1]/289.13, 0.5 + tran_vec[2]*0.0036754 , 0.5 + tran_vec[0]/313.7255]

						# 		# Extract translation
						# 		translation = transform_matrix[:3,3]
					
						# 		# Set view rotation with ? axis and 30 degrees
						# 		rot_matrix_x = rotation_matrix_x(45)
						# 		rot_matrix_y = rotation_matrix_y(45)
						# 		rot_matrix_z = rotation_matrix_z(45)
						# 		# The rotation matrix part
						# 		rotation_matrix = transform_matrix[:3,:3]
						# 		# Change the view if observation
						# 		rotation_matrix = rotation_matrix @ rot_matrix_x
						# 		vector = rotation_matrix_to_vector(rotation_matrix)
						# 		testbed.view_dir = vector

						# 		#Get up_dir
						# 		vector_up = [rotation_matrix[1, 1], rotation_matrix[2, 1],rotation_matrix[0, 1]]
						# 		testbed.up_dir = vector_up

						# 		testbed.scale = 1.0
						# 		print("third: ", testbed.camera_matrix)
						# 		testbed.reset_accumulation()

                        # TODO: Evaluation with baseboard position

						
							



						#print("testbed.frame:", testbed.frame)
						#print("testbed.first_training_view:", testbed.first_training_view)

						
						
						


						# TODO: add the following transforms in `world_extrinsic_mapping.py` or find a way to simplify the procedure
						# TODO: change testbed.look_at/ testbed.view_dir/ testbed.scale and add constrain to the view
						# TODO: compare the camera ground truth with the generated viewpoint

				if testbed.want_repl():
					repl(testbed)
				# What will happen when training is done?
				if testbed.training_step >= n_steps:
					if args.gui:
						testbed.shall_train = False
					else:
						break

				# Update progress bar
				if testbed.training_step < old_training_step or old_training_step == 0:
					old_training_step = 0
					t.reset()

				now = time.monotonic()
				if now - tqdm_last_update > 0.1:
					t.update(testbed.training_step - old_training_step)
					t.set_postfix(loss=testbed.loss)
					old_training_step = testbed.training_step
					tqdm_last_update = now

	with open(args.evaluation_list, 'w', newline='') as file:
		writer = csv.writer(file)
		writer.writerows(stream_list)
		writer.writerows([[time] for time in times_recorded])

	if args.save_snapshot:
		testbed.save_snapshot(args.save_snapshot, False)

	if args.test_transforms:
		print("Evaluating test transforms from ", args.test_transforms)
		with open(args.test_transforms) as f:
			test_transforms = json.load(f)
		data_dir=os.path.dirname(args.test_transforms)
		totmse = 0
		totpsnr = 0
		totssim = 0
		totcount = 0
		minpsnr = 1000
		maxpsnr = 0

		# Evaluate metrics on black background
		testbed.background_color = [0.0, 0.0, 0.0, 1.0]

		# Prior nerf papers don't typically do multi-sample anti aliasing.
		# So snap all pixels to the pixel centers.
		testbed.snap_to_pixel_centers = True
		spp = 8

		testbed.nerf.render_min_transmittance = 1e-4

		testbed.shall_train = False
		testbed.load_training_data(args.test_transforms)

		with tqdm(range(testbed.nerf.training.dataset.n_images), unit="images", desc=f"Rendering test frame") as t:
			for i in t:
				resolution = testbed.nerf.training.dataset.metadata[i].resolution
				testbed.render_ground_truth = True
				testbed.set_camera_to_training_view(i)
				ref_image = testbed.render(resolution[0], resolution[1], 1, True)
				testbed.render_ground_truth = False
				image = testbed.render(resolution[0], resolution[1], spp, True)

				if i == 0:
					write_image(f"ref.png", ref_image)
					write_image(f"out.png", image)

					diffimg = np.absolute(image - ref_image)
					diffimg[...,3:4] = 1.0
					write_image("diff.png", diffimg)

				A = np.clip(linear_to_srgb(image[...,:3]), 0.0, 1.0)
				R = np.clip(linear_to_srgb(ref_image[...,:3]), 0.0, 1.0)
				mse = float(compute_error("MSE", A, R))
				ssim = float(compute_error("SSIM", A, R))
				totssim += ssim
				totmse += mse
				psnr = mse2psnr(mse)
				totpsnr += psnr
				minpsnr = psnr if psnr<minpsnr else minpsnr
				maxpsnr = psnr if psnr>maxpsnr else maxpsnr
				totcount = totcount+1
				t.set_postfix(psnr = totpsnr/(totcount or 1))

		psnr_avgmse = mse2psnr(totmse/(totcount or 1))
		psnr = totpsnr/(totcount or 1)
		ssim = totssim/(totcount or 1)
		print(f"PSNR={psnr} [min={minpsnr} max={maxpsnr}] SSIM={ssim}")

	if args.save_mesh:
		res = args.marching_cubes_res or 256
		print(f"Generating mesh via marching cubes and saving to {args.save_mesh}. Resolution=[{res},{res},{res}]")
		testbed.compute_and_save_marching_cubes_mesh(args.save_mesh, [res, res, res])

	if ref_transforms:
		testbed.fov_axis = 0
		testbed.fov = ref_transforms["camera_angle_x"] * 180 / np.pi
		if not args.screenshot_frames:
			args.screenshot_frames = range(len(ref_transforms["frames"]))
		print(args.screenshot_frames)
		for idx in args.screenshot_frames:
			f = ref_transforms["frames"][int(idx)]
			cam_matrix = f["transform_matrix"]
			testbed.set_nerf_camera_matrix(np.matrix(cam_matrix)[:-1,:])
			outname = os.path.join(args.screenshot_dir, os.path.basename(f["file_path"]))

			# Some NeRF datasets lack the .png suffix in the dataset metadata
			if not os.path.splitext(outname)[1]:
				outname = outname + ".png"

			print(f"rendering {outname}")
			image = testbed.render(args.width or int(ref_transforms["w"]), args.height or int(ref_transforms["h"]), args.screenshot_spp, True)
			os.makedirs(os.path.dirname(outname), exist_ok=True)
			write_image(outname, image)
	elif args.screenshot_dir:
		outname = os.path.join(args.screenshot_dir, args.scene + "_" + network_stem)
		print(f"Rendering {outname}.png")
		image = testbed.render(args.width or 1920, args.height or 1080, args.screenshot_spp, True)
		if os.path.dirname(outname) != "":
			os.makedirs(os.path.dirname(outname), exist_ok=True)
		write_image(outname + ".png", image)

	if args.video_camera_path:
		testbed.load_camera_path(args.video_camera_path)

		resolution = [args.width or 1920, args.height or 1080]
		n_frames = args.video_n_seconds * args.video_fps
		save_frames = "%" in args.video_output
		start_frame, end_frame = args.video_render_range

		if "tmp" in os.listdir():
			shutil.rmtree("tmp")
		os.makedirs("tmp")

		for i in tqdm(list(range(min(n_frames, n_frames+1))), unit="frames", desc=f"Rendering video"):
			testbed.camera_smoothing = args.video_camera_smoothing

			if start_frame >= 0 and i < start_frame:
				# For camera smoothing and motion blur to work, we cannot just start rendering
				# from middle of the sequence. Instead we render a very small image and discard it
				# for these initial frames.
				# TODO Replace this with a no-op render method once it's available
				frame = testbed.render(32, 32, 1, True, float(i)/n_frames, float(i + 1)/n_frames, args.video_fps, shutter_fraction=0.5)
				continue
			elif end_frame >= 0 and i > end_frame:
				continue

			frame = testbed.render(resolution[0], resolution[1], args.video_spp, True, float(i)/n_frames, float(i + 1)/n_frames, args.video_fps, shutter_fraction=0.5)
			if save_frames:
				write_image(args.video_output % i, np.clip(frame * 2**args.exposure, 0.0, 1.0), quality=100)
			else:
				write_image(f"tmp/{i:04d}.jpg", np.clip(frame * 2**args.exposure, 0.0, 1.0), quality=100)

		if not save_frames:
			os.system(f"ffmpeg -y -framerate {args.video_fps} -i tmp/%04d.jpg -c:v libx264 -pix_fmt yuv420p {args.video_output}")

		shutil.rmtree("tmp")
