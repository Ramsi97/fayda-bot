#!/usr/bin/env python3
"""
Offline ID-card render test — NO network / NO OTP.

Reads a captured Fayda profile (the same JSON shape the API returns under
`exchangeResident`) and renders the ID cards exactly the way the bot does in
services/processing_service.py, so you can eyeball the result without the live
flow.

Usage (from the project root, with your venv):

    python3 test_offline_render.py
    python3 test_offline_render.py --profile /path/to/resident.json --fan 5906107917458215
    python3 test_offline_render.py --template B --bw

Outputs PNGs into ./test_output/ and prints their paths.
"""
import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from core.api.fayda_client import FaydaClient
from core.image.qr_service import generate_fayda_qr, generate_code128_barcode
from core.image.image_generator import generate_final_id_image
from core.image.image_generator_b import generate_final_id_image_b

# Default to the captured reference profile; override with --profile.
DEFAULT_PROFILE = "/home/ramsi/Desktop/cyber/reverse_Eng/fayda/scrpits/fayda_out/resident.json"
# The FAN (16-digit card number) is the user-typed login — it is NOT in the
# profile JSON, so we supply a sample. Override with --fan.
DEFAULT_FAN = "5906107917458215"

FONT_AM = "./fonts/truetype/abyssinica/AbyssinicaSIL-Regular.ttf"
FONT_EN = "./fonts/truetype/noto/NotoSans-Regular.ttf"


def render(profile: dict, fan: str, template: str, color: bool):
    """Mirror processing_service.verify_and_generate (steps 3-6), offline.

    Returns (card_png_bytes, qr_img) so the caller can sanity-check the QR from
    the full-size QR image (decoding the shrunk-down copy on the card is flaky).
    """
    fin = str(((profile.get("data") or {}).get("identity") or {}).get("UIN") or "")
    text_data = FaydaClient.profile_to_text_data(profile, fan=fan, fin=fin)

    photo_bytes = FaydaClient.extract_photo_bytes(profile)
    photo_np = None
    if photo_bytes:
        photo_np = cv2.imdecode(np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR)

    # QR — A: field carries the 16-digit FAN (same as the bot)
    qr_fan = fan or text_data.get("fan_code", "")
    dob_val = text_data.get("date_of_birth_greg") or "2000/01/01"
    qr_img = generate_fayda_qr(
        fan=qr_fan,
        name=text_data.get("name_en", "Resident"),
        dob=dob_val,
        gender=text_data.get("sex_en", "M"),
        photo_b64=profile.get("photo"),
    )

    barcode_img = generate_code128_barcode(fan or fin)

    generator = generate_final_id_image_b if template == "B" else generate_final_id_image
    card = generator(
        text_data=text_data,
        photo=photo_np,
        qrcode_img=qr_img,
        barcode_img=barcode_img,
        font_amharic=FONT_AM,
        font_english=FONT_EN,
        font_size=17,
        boldness=0.5,
        color=color,
    )
    return card, qr_img


def decode_qr_a_field(qr_img: Image.Image) -> str | None:
    """Pull the A: field out of the standalone QR image, for a quick sanity check."""
    arr = cv2.cvtColor(np.array(qr_img.convert("RGB")), cv2.COLOR_RGB2BGR)
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(arr)
    if not data:
        return None
    parts = data.split(":")
    for i, p in enumerate(parts):
        if p == "A" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline Fayda ID-card render test")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="path to a captured resident.json")
    ap.add_argument("--fan", default=DEFAULT_FAN, help="16-digit FAN (card number)")
    ap.add_argument("--template", choices=["A", "B", "both"], default="both")
    ap.add_argument("--bw", action="store_true", help="render black & white instead of color")
    ap.add_argument("--outdir", default="test_output")
    args = ap.parse_args()

    profile_path = Path(args.profile)
    if not profile_path.exists():
        print(f"❌ Profile not found: {profile_path}")
        print("   Pass one with --profile /path/to/resident.json")
        return 1

    with open(profile_path) as f:
        profile = json.load(f)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    identity = (profile.get("data") or {}).get("identity") or {}
    print(f"Profile : {profile_path}")
    print(f"UIN/FIN : {identity.get('UIN')}")
    print(f"FAN     : {args.fan}")
    print(f"Mode    : {'B&W' if args.bw else 'Color'}")
    print("-" * 50)

    templates = ["A", "B"] if args.template == "both" else [args.template]
    color = not args.bw

    for tpl in templates:
        try:
            card, qr_img = render(profile, args.fan, tpl, color)
        except Exception as e:
            import traceback
            print(f"❌ Template {tpl} failed: {e}")
            traceback.print_exc()
            continue

        im = Image.open(BytesIO(card))
        suffix = "bw" if args.bw else "color"
        out = outdir / f"card_{tpl}_{suffix}.png"
        im.save(out)

        a_field = decode_qr_a_field(qr_img)
        qr_ok = "✅ FAN" if a_field == args.fan else f"⚠️ got {a_field!r}"
        print(f"✅ Template {tpl}: {im.size[0]}x{im.size[1]}  ->  {out}")
        print(f"   QR A: field = {a_field}  ({qr_ok})")

    print("-" * 50)
    print(f"Open the PNGs in {outdir}/ to inspect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
