from pathlib import Path
from core.image.image_generator import generate_final_id_image

pdf_path = Path("/home/ramsi/Desktop/projects/updated_bot/storage/temp/efayda_Basha Wayu Bancha.pdf")

output_dir = Path("/home/ramsi/Desktop/projects/updated_bot/storage/temp")

final_bytes = generate_final_id_image(pdf_path, output_dir)


# For local test:
with open("/home/ramsi/Desktop/projects/updated_bot/storage/new_final.png", "wb") as f:
    f.write(final_bytes)

print("✅ Final image saved for review!")
