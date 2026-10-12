import os
import sys
from pathlib import Path
from PIL import Image
from io import BytesIO

# Add project root to path
sys.path.append(os.getcwd())

from core.image.image_generator import generate_final_id_image

def test_generation():
    # Use one of the existing sample PDFs
    sample_pdf = None
    possible_samples = [
        "storage/temp/efayda_Basha Wayu Bancha.pdf",
        "storage/uploads/efayda_Manyazewal Bekele Weldeyes.pdf",
        "storage/temp/efayda_Basha Wayu Bancha.pdf"
    ]
    
    for s in possible_samples:
        if Path(s).exists():
            sample_pdf = Path(s)
            break
            
    if not sample_pdf:
        print("❌ No sample PDF found in storage/uploads/ or storage/temp/")
        return

    output_dir = Path("storage/temp/test_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating ID from {sample_pdf}...")
    try:
        image_bytes = generate_final_id_image(
            pdf_path=sample_pdf,
            output_dir=output_dir,
            font_amharic="./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf",
            font_english="./fonts/truetype/noto/NotoSans-Regular.ttf",
            font_size=17,
            boldness=0.5,
            color=True
        )

        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        print(f"Generated image size: {width}x{height}")

        target_w, target_h = 1280, 390
        if width == target_w and height == target_h:
            print(f"✅ SUCCESS: Dimensions match White Template Cur target ({target_w}x{target_h})")
            # Save for manual inspection
            save_path = "storage/test_result_high_res.png"
            img.save(save_path)
            print(f"Saved for verification to: {save_path}")
        else:
            print(f"❌ FAILURE: Expected {target_w}x{target_h}, got {width}x{height}")

    except Exception as e:
        print(f"❌ Error during generation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_generation()
