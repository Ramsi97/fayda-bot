import os
import sys
from pathlib import Path
from PIL import Image
from io import BytesIO

# Add project root to path
sys.path.append(os.getcwd())

from core.image.image_generator_b import generate_final_id_image_b

def test_generation_b():
    # Use one of the existing sample PDFs
    sample_pdf = None
    possible_samples = [
        "storage/temp/efayda_Basha Wayu Bancha.pdf",
        "storage/uploads/gebre.pdf",
        "data/sample.pdf"
    ]
    
    for s in possible_samples:
        if Path(s).exists():
            sample_pdf = Path(s)
            break
            
    if not sample_pdf:
        # Try to find ANY pdf
        pdfs = list(Path(".").rglob("*.pdf"))
        if pdfs:
            sample_pdf = pdfs[0]

    if not sample_pdf:
        print("❌ No sample PDF found to test with.")
        return

    output_dir = Path("storage/temp/test_output_b")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating Template Black Cur ID from {sample_pdf}...")
    try:
        image_bytes = generate_final_id_image_b(
            pdf_path=sample_pdf,
            output_dir=output_dir,
            font_amharic="./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf",
            font_english="./fonts/truetype/noto/NotoSans-Regular.ttf",
            color=True
        )

        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        print(f"Generated Template Black Cur image size: {width}x{height}")

        # Template Black Cur target size is 1280x390
        target_w, target_h = 1280, 390
        if width == target_w and height == target_h:
            print(f"✅ SUCCESS: Dimensions match Template Black Cur target ({target_w}x{target_h})")
            # Save for manual inspection
            save_path = "storage/test_result_template_b.png"
            img.save(save_path)
            print(f"Saved for verification to: {os.path.abspath(save_path)}")
        else:
            print(f"❌ FAILURE: Expected {target_w}x{target_h}, got {width}x{height}")
            # Still save it to see what happened
            save_path = "storage/test_result_template_b_fail.png"
            img.save(save_path)
            print(f"Saved failed result to: {os.path.abspath(save_path)}")

    except Exception as e:
        print(f"❌ Error during generation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_generation_b()
