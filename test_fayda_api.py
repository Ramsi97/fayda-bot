# test_fayda_api.py
"""
Interactive CLI test for the Fayda API -> QR Code -> ID Card pipeline.

Usage:
    /home/ramsi/telegram_bot/myenv/bin/python3 test_fayda_api.py <FAN_NUMBER> <FIN_NUMBER> [TEMPLATE A or B]

Example:
    /home/ramsi/telegram_bot/myenv/bin/python3 test_fayda_api.py 1234567890 5906107917458215 A
"""

import asyncio
import sys
import cv2
import numpy as np
from pathlib import Path

from core.api.fayda_client import FaydaClient
from core.image.qr_service import generate_fayda_qr, generate_code128_barcode
from core.image.image_generator import generate_final_id_image
from core.image.image_generator_b import generate_final_id_image_b


async def test_pipeline(fan: str, fin: str, template: str = "A"):
    client = FaydaClient()

    print(f"\n=======================================================")
    print(f"🚀 Testing Fayda Pipeline: FAN={fan} | FIN={fin} | Template={template}")
    print(f"=======================================================\n")

    # 1. Initiate session
    print("[1/5] Initiating OAuth & requesting CSRF...")
    session = await client.initiate(fan)
    print(f"  Transaction ID: {session.txn_id}")

    # 2. Send OTP
    print("[2/5] Triggering OTP to phone...")
    send_resp = await client.send_otp(session)
    print("  OTP sent successfully!")

    # 3. Read OTP interactively
    otp = input("\n👉 Enter the OTP received on your phone: ").strip()

    # 4. Verify OTP & get profile
    print("\n[3/5] Verifying OTP and exchanging for tokens...")
    await client.verify_otp(session, otp)
    print("  OTP verified! Fetching resident profile...")

    profile = await client.fetch_profile(session)
    await session.close()

    text_data = FaydaClient.profile_to_text_data(profile, fan=fan, fin=fin)
    print("\n[4/5] Profile data extracted:")
    for k, v in text_data.items():
        print(f"  {k}: {v}")

    # Process photo
    photo_bytes = FaydaClient.extract_photo_bytes(profile)
    photo_np = None
    if photo_bytes:
        photo_np = cv2.imdecode(np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR)
        print(f"  Photo loaded: {len(photo_bytes)} bytes")

    # 5. Generate QR & Barcode
    print("\n[5/5] Generating QR code, Barcode, and ID Card...")
    dob_val = text_data.get("date_of_birth_greg") or "2000/01/01"
    qr_img = generate_fayda_qr(
        fan=fan,
        name=text_data.get("name_en", "Resident"),
        dob=dob_val,
        gender=text_data.get("sex_en", "M"),
        photo_b64=profile.get("photo"),
    )
    barcode_img = generate_code128_barcode(fan or fin)

    # 6. Render Card
    generator_func = generate_final_id_image_b if template == "B" else generate_final_id_image
    image_bytes = generator_func(
        text_data=text_data,
        photo=photo_np,
        qrcode_img=qr_img,
        barcode_img=barcode_img,
        font_amharic="./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf",
        font_english="./fonts/truetype/noto/NotoSans-Regular.ttf",
        font_size=17,
        boldness=0.5,
        color=True,
    )

    out_file = Path(f"test_id_output_{template}.png")
    out_file.write_bytes(image_bytes)
    print(f"\n✅ ID Card generated successfully! Saved to: {out_file.resolve()}\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 test_fayda_api.py <FAN_NUMBER> <FIN_NUMBER> [TEMPLATE A or B]")
        sys.exit(1)

    fan_arg = sys.argv[1]
    fin_arg = sys.argv[2]
    tpl_arg = sys.argv[3].upper() if len(sys.argv) > 3 else "A"

    asyncio.run(test_pipeline(fan_arg, fin_arg, tpl_arg))
