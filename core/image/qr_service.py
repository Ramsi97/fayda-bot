# core/image/qr_service.py
"""
QR Code and Barcode generator for Fayda ID cards.
Implements the Fayda QR payload format and detached JWS signing as defined in qr_generator.py.
"""

import base64
import json
import os
from io import BytesIO
from typing import Optional
from PIL import Image, ImageOps
import qrcode
import barcode
from barcode.writer import ImageWriter

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KEY_FILE = "data/local_signing_key.pem"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def get_or_create_signing_key():
    """Load or generate a local RSA key for signing the Fayda QR payload."""
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "rb") as f:
                return serialization.load_pem_private_key(f.read(), None)
        except Exception:
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(KEY_FILE, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return key


def _sign_detached_rs256(payload: str, key) -> str:
    header = _b64url_encode(
        json.dumps({"alg": "RS256"}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}..{_b64url_encode(sig)}"


# The real Fayda QR embeds the face as a TINY WebP (78x100, a few hundred bytes),
# URL-safe base64 encoded — not the full API JPEG. Matching this keeps the whole
# payload inside a low QR version so it stays scannable. (Ref: id_image.webp /
# the captured REAL_PAYLOAD = 78x100 WebP, 334 bytes, QR version 21.)
QR_PHOTO_SIZE = (78, 100)
QR_PHOTO_MAX_BYTES = 1500       # byte budget so photo+fields+JWS fit a low QR version
_QR_WEBP_QUALITIES = (8, 5, 3, 1)


def _photo_to_qr_webp_b64(api_photo_b64: Optional[str]) -> str:
    """Convert the API photo (standard-base64 JPEG) into the form the Fayda QR
    embeds: a 78x100 WebP thumbnail, URL-safe base64, padding stripped.

    Returns "" if there is no photo or it can't be decoded.
    """
    if not api_photo_b64:
        return ""
    try:
        raw = base64.b64decode(api_photo_b64 + "=" * (-len(api_photo_b64) % 4))
    except Exception:
        return ""
    im = None
    try:
        im = Image.open(BytesIO(raw)).convert("RGB")
    except Exception:
        # Some Fayda photos (e.g. JP2) need OpenCV to decode.
        try:
            import cv2
            import numpy as np
            arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if arr is None:
                return ""
            im = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
        except Exception:
            return ""

    small = ImageOps.fit(im, QR_PHOTO_SIZE, Image.Resampling.LANCZOS)
    data = b""
    for quality in _QR_WEBP_QUALITIES:
        buf = BytesIO()
        small.save(buf, format="WEBP", quality=quality, method=6)
        data = buf.getvalue()
        if len(data) <= QR_PHOTO_MAX_BYTES:
            break
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate_fayda_qr(
    fan: str,
    name: str,
    dob: str,
    gender: str = "M",
    photo_b64: Optional[str] = None,
) -> Image.Image:
    """
    Generate a Fayda QR code image.
    Format: {photo_webp_b64url}:DLT:{name}:V:4:G:{gender}:A:{fan}:D:{dob}:SIGN:{jws}

    The ``A:`` field carries the 16-digit FAN (card number), matching the real
    Fayda QR payload (e.g. A:5906107917458215). The photo is embedded as a tiny
    78x100 WebP (URL-safe base64), exactly like the real Fayda QR. The JWS is
    signed with a LOCAL key (we don't hold the issuer's key), so the QR is
    structurally valid and scannable but not issuer-verifiable.
    """
    # Normalize gender (M/F)
    g = gender.upper()[:1] if gender else "M"
    if g not in ("M", "F"):
        g = "M"

    key = get_or_create_signing_key()
    core = f"DLT:{name}:V:4:G:{g}:A:{fan}:D:{dob}"

    def _build(photo_part: str) -> str:
        payload = f"{photo_part}:{core}" if photo_part else core
        jws = _sign_detached_rs256(payload, key)
        return f"{payload}:SIGN:{jws}"

    photo_qr = _photo_to_qr_webp_b64(photo_b64)

    def _render(full_payload: str) -> Image.Image:
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=8,
            border=2,
        )
        qr.add_data(full_payload)
        qr.make(fit=True)   # raises ValueError if it can't fit in version<=40
        return qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    try:
        return _render(_build(photo_qr))
    except Exception as e:
        # Last-resort: if the photo still pushes it past QR capacity, drop the
        # photo so a valid (fieldsonly) QR is always produced rather than none.
        print(f"[Warning] QR with photo overflowed ({e}); retrying without photo.")
        return _render(_build(""))


def generate_code128_barcode(data: str) -> Image.Image:
    """Generate a clean 1D Code128 barcode image (no text underneath)."""
    try:
        code128 = barcode.get("code128", data, writer=ImageWriter())
        fp = BytesIO()
        code128.write(fp, options={"write_text": False, "quiet_zone": 1.0})
        fp.seek(0)
        return Image.open(fp).convert("RGBA")
    except Exception as e:
        print(f"[Warning] Failed to generate barcode for {data}: {e}")
        # Return a blank transparent image
        return Image.new("RGBA", (200, 50), (255, 255, 255, 0))
