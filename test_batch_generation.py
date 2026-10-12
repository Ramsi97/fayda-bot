import os
import sys
import io
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageOps
from io import BytesIO
import tempfile

# Add project root to path
sys.path.append(os.getcwd())

from core.image.image_generator import generate_final_id_image

def test_batch_generation():
    # 1. Find a sample PDF
    sample_pdf = None
    possible_samples = [
        "storage/temp/efayda_Basha Wayu Bancha.pdf",
        "storage/uploads/gebre.pdf"
    ]
    
    for s in possible_samples:
        if Path(s).exists():
            sample_pdf = Path(s)
            break
            
    if not sample_pdf:
        # Search for any PDF
        pdfs = list(Path(".").rglob("*.pdf"))
        if pdfs:
            sample_pdf = pdfs[0]
            
    if not sample_pdf:
        print("❌ No sample PDF found to test with.")
        return

    print(f"📂 Using sample PDF: {sample_pdf}")

    # Settings matching processing_service.py
    A4_WIDTH = 905
    A4_HEIGHT = 1280
    TARGET_HEIGHT = 230
    TARGET_ROW_WIDTH = 820
    # --------------------------------------------------

    output_dir = Path("storage/temp/batch_test")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Generate the single image once for testing
        print(f"🔄 Generating ID image...")
        image_bytes = generate_final_id_image(
            pdf_path=sample_pdf,
            output_dir=output_dir,
            font_amharic="./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf",
            font_english="./fonts/truetype/noto/NotoSans-Regular.ttf",
            color=True
        )

        # Reorder and Layout
        full_id_img = Image.open(io.BytesIO(image_bytes))
        id_w, id_h = full_id_img.size
        print(f"📏 Single ID image size: {id_w}x{id_h}")

        front_raw = full_id_img.crop((0, 0, id_w // 2, id_h))
        back_raw = full_id_img.crop((id_w // 2, 0, id_w, id_h))
        
        # Robust helper to trim ALL whitespace using threshold
        def trim_all(im, threshold=10):
            gray = im.convert("L")
            inv = ImageOps.invert(gray)
            bbox = inv.point(lambda p: p > threshold and 255).getbbox()
            if not bbox: return im
            return im.crop(bbox)

        # Use the same target sizes as processing_service
        ID_TARGET_W = 388
        ID_TARGET_H = 244
        GAP = 28
        TARGET_ROW_WIDTH = (ID_TARGET_W * 2) + GAP
        TARGET_HEIGHT = ID_TARGET_H

        front = trim_all(front_raw).resize((ID_TARGET_W, ID_TARGET_H), Image.Resampling.LANCZOS)
        back = trim_all(back_raw).resize((ID_TARGET_W, ID_TARGET_H), Image.Resampling.LANCZOS)
        
        # Create the new row
        new_row = Image.new('RGB', (TARGET_ROW_WIDTH, TARGET_HEIGHT), (255, 255, 255))
        new_row.paste(front, (0, 0))
        new_row.paste(back, (ID_TARGET_W + GAP, 0))
        
        # Simulate a batch of 5
        num_ids = 5
        a4_canvas = Image.new('RGB', (A4_WIDTH, A4_HEIGHT), (255, 255, 255))
        
        v_gap = 10
        total_block_h = (num_ids * TARGET_HEIGHT) + ((num_ids - 1) * v_gap)
        start_y = (A4_HEIGHT - total_block_h) // 2
        x_pos = (A4_WIDTH - TARGET_ROW_WIDTH) // 2
        
        print(f"📄 Arranging {num_ids} IDs on A4 page...")
        for j in range(num_ids):
            y_pos = start_y + j * (TARGET_HEIGHT + v_gap)
            a4_canvas.paste(new_row, (x_pos, y_pos))

        # Apply mirroring for printing
        a4_canvas = a4_canvas.transpose(Image.FLIP_LEFT_RIGHT)

        # Save result
        save_path = "storage/batch_test_result_A4_mirrored.png"
        a4_canvas.save(save_path)
        print(f"✅ SUCCESS: Saved A4 test result to: {os.path.abspath(save_path)}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_batch_generation()
