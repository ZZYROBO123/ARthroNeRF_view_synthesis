# Usage:
#
# python3 script.py --input original.png --output modified.png
# Based on: https://github.com/mostafaGwely/Structural-Similarity-Index-SSIM-

# 1. Import the necessary packages
#from skimage.measure import compare_ssim
from skimage.metrics import structural_similarity as ssim
import argparse
import imutils
import cv2
import os

path = 'black_toy'

# # 2. Construct the argument parse and parse the arguments
# ap = argparse.ArgumentParser()
# ap.add_argument("-f", "--first", required=True, help="Directory of the image that will be compared")
# ap.add_argument("-s", "--second", required=True, help="Directory of the image that will be used to compare")
# args = vars(ap.parse_args())

# 3. Load the two input images
imageA = cv2.imread("black_toy/images/564.png")
imageB = cv2.imread("black_toy/test_output/564.png", 1)



# 4. Convert the images to grayscale
grayA = cv2.cvtColor(imageA, cv2.COLOR_BGR2GRAY)
grayB = cv2.cvtColor(imageB, cv2.COLOR_BGR2GRAY)

# 5. Compute the Structural Similarity Index (SSIM) between the two
#    images, ensuring that the difference image is returned
#(score, diff) = compare_ssim(grayA, grayB, full=True)
(score, diff) = ssim(grayA, grayB, full=True)
diff = (diff * 255).astype("uint8")

# 6. You can print only the score if you want
print("SSIM: {}".format(score))

images = os.listdir(path+'/test_output')
total = []
for image in images:
    generated = path + '/test_output/' + image
    original =  path + '/images/' + image
    generated = cv2.imread(generated)
    original = cv2.imread(original, 1)
    (score, diff) = ssim(grayA, grayB, full=True)
    diff = (diff * 255).astype("uint8")
    total.append(score)
print(sum(total)/len(total))