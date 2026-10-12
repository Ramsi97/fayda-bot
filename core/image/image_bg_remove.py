from rembg import remove
from PIL import Image
import numpy as np
import cv2
import io

def get_image_without_bg(input_image):
    """
    Accepts a PIL Image or a NumPy (OpenCV) array.
    Removes the background and returns a PIL RGBA Image.
    """
    # 1. If the input is a NumPy array (OpenCV), convert BGR to RGB
    if isinstance(input_image, np.ndarray):
        # OpenCV uses BGR, but rembg/PIL use RGB
        input_image = cv2.cvtColor(input_image, cv2.COLOR_BGR2RGB)
        input_image = Image.fromarray(input_image)

    # 2. rembg.remove can take a PIL image directly and returns a PIL image
    output_image = remove(input_image)

    # 3. Ensure the result is in RGBA mode (to support transparency)
    return output_image.convert("RGBA")