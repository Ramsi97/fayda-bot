from PIL import Image

def overlay_images(background_img, foreground_img, position=(0, 0)):
    """
    Pastes one image onto another. Both inputs must be PIL Image objects.
    """
    # 1. Ensure the background has an Alpha channel (transparency support)
    # We use .copy() so we don't modify the original background object
    combined = background_img.copy().convert("RGBA")
    
    # 2. Ensure the foreground is in RGBA (so it has the transparency mask)
    fg = foreground_img.convert("RGBA")

    # 3. Paste the foreground onto the background
    # The third argument (fg) acts as the transparency mask
    combined.paste(fg, position, fg)

    # 4. Convert back to RGB (standard for JPG/Saving)
    return combined.convert("RGB")