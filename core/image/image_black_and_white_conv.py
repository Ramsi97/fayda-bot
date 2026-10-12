import cv2
import numpy as np
from PIL import Image

def get_grayscale_image(input_image):
    """
    Converts a color image to Grayscale (shades of gray).
    Accepts PIL Image or NumPy array.
    """
    # 1. If it's a PIL image, convert to NumPy array
    if isinstance(input_image, Image.Image):
        input_image = np.array(input_image)

    # 2. Convert to Grayscale
    if len(input_image.shape) == 3:  # If the image has color channels
        if input_image.shape[2] == 3:
            # Most PDF images are RGB, convert to Gray
            gray_image = cv2.cvtColor(input_image, cv2.COLOR_RGB2GRAY)
        elif input_image.shape[2] == 4:
            # If it has transparency (RGBA)
            gray_image = cv2.cvtColor(input_image, cv2.COLOR_RGBA2GRAY)
    else:
        # Image is already grayscale
        gray_image = input_image
    return gray_image