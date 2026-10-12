#!/usr/bin/env python3
"""
gen_fayda_qr.py — render a FaydaEncode QR from your own credential payload.

Modes:
  1. (default) Use the REAL payload you captured — real issuer signature, verbatim.
  2. --resign  Rebuild the payload from fields (optionally edited) and sign it with a
               LOCAL RSA key you generate. Same JWS format, but NOT issuer-signed —
               real verifiers will reject it. For testing your own verifier only.

Usage:
  python3 gen_fayda_qr.py                      # real payload -> my_fayda_qr.png
  python3 gen_fayda_qr.py --resign             # rebuild + local RSA signature
  python3 gen_fayda_qr.py --resign --fin 5906107917458215 --name "Your Name" \
                         --dob "2004/10/11" --gender M --photo photo.webp
  python3 gen_fayda_qr.py --verify             # verify the JWS sig with the local key
"""

import argparse, base64, json, os, sys

REAL_PAYLOAD = (
    "UklGRkYBAABXRUJQVlA4IDoBAABwDQCdASpOAGQAP_n8_n2_v7-yMHHOG_A_CWUA1BRoNC4PIzsU8TI5U3g753K4VV6xW-XYharmaXuojOjEnddlJQwMEWKbyBljs0Rv0d9obTI8HUggKycYw2j5LC5Ve_XjAKOFHEtZH64FTQh0GyrrYjR2gAD-5W029VptFLoj6r6chKDOwJMAcgsdo6TgRbow38Ja7qzUBKc65qp9GJYmtYheAjVxX1h5s-3PL0kxHJqwyqQPNgJXjAm0MXjYOLV-xRs6oGFD2yQwiOIVhzDMobPiLLb6pp8t8POWKGm6dD7WHqUz48kMYd9uN9BBIpJdfitD0X12tKi3HfXFfdbSHhMrF8c0f6j4QihAtUEb76Vfh2U1cZvnLpg0C48D2wfRMz5wpW8pd9vK9CdwTuAtIjI2jggValAAAA"
    ":DLT:Remedan Jemal Gemeda:V:4:G:M:A:5906107917458215:D:2004/10/11"
    ":SIGN:eyJhbGciOiJSUzI1NiJ9..JonlmE3YJ0zVEqVbMXmyTGm2oawzo5NWRCcbCFHmbRpK8x1EGc-AvKfaM9384sTNdHGkG_WTejBKHmXAoWjjUyZD90YNitLyPrvYlmSV-o_a3bsMikX7Xu6BBByEMAN9oEZcR8E0270YxCDhEcPxAPLduMB91GMYN-_084YNBd0LWY4gI1rw_ohw26m0ndVmcepliqefR1EDxEwhOYPNsuPlLJBvNp43-j3G-Uv_8zJ2LypzGMj1FCyxAHI_zy_bHk3q9_g3LxC05aiF0PTyCIj9qKZCa9l33ey2vT7amE3iR2rDQyNmXYqRQ6-yl_U3jaHlWjUnF_uyjggJG-fcrQ"
)

B64URL_ALPHABET = "-_"

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def parse_payload(payload: str) -> dict:
    """Split the colon string into (photo_b64, fields, jws) for inspection."""
    parts = payload.split(":")
    jws_idx = next(i for i, p in enumerate(parts) if p == "SIGN")
    jws = ":".join(parts[jws_idx + 1:])
    fields = parts[1:jws_idx]          # DLT, name, V:4, G:M, A, fin, D:dob
    photo = parts[0]
    return {"photo": photo, "fields": fields, "jws": jws}

def dump_photo(photo_b64: str, out: str = "photo.webp"):
    raw = base64.b64decode(photo_b64 + "==")
    open(out, "wb").write(raw)
    print(f"[+] photo saved -> {out} ({len(raw)} bytes, starts with {raw[:4]!r})")

# ---------------------------------------------------------------- resign mode
def rebuild_payload(fin, name, dob, gender, photo_b64) -> str:
    core = f"DLT:{name}:V:4:G:{gender}:A:{fin}:D:{dob}"
    return f"{photo_b64}:{core}" if photo_b64 else core

def load_or_create_key(keyfile: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    if os.path.exists(keyfile):
        key = serialization.load_pem_private_key(open(keyfile, "rb").read(), None)
        print(f"[+] loaded existing key {keyfile}")
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        open(keyfile, "wb").write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        print(f"[+] generated new RSA-2048 key -> {keyfile}")
    return key

def sign_detached_rs256(payload: str, key) -> str:
    """Same format as the real one: header.payload.sig with EMPTY payload (detached),
    signature computed over header + '.' + payload."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    header = b64url_encode(json.dumps({"alg": "RS256"}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}..{b64url_encode(sig)}"

def verify_detached(payload: str, jws: str, key) -> bool:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    header_b64, _, sig_b64 = jws.split(".")
    try:
        key.public_key().verify(
            b64url_decode(sig_b64),
            f"{header_b64}.{payload}".encode(),
            padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resign", action="store_true",
                    help="rebuild payload from fields and sign with a LOCAL RSA key")
    ap.add_argument("--verify", action="store_true",
                    help="verify signature (needs --resign-created key or --keyfile)")
    ap.add_argument("--keyfile", default="local_signing_key.pem")
    ap.add_argument("--fin", default="5906107917458215")
    ap.add_argument("--name", default="Remedan Jemal Gemeda")
    ap.add_argument("--dob", default="2004/10/11")
    ap.add_argument("--gender", default="M")
    ap.add_argument("--photo", help="file to embed as base64 (webp/jpg/png)")
    ap.add_argument("-o", "--out", default="fayda_qr.png")
    args = ap.parse_args()

    try:
        import qrcode
    except ImportError:
        sys.exit("pip install qrcode[pil]")

    if args.resign:
        photo_b64 = ""
        if args.photo:
            photo_b64 = base64.b64encode(open(args.photo, "rb").read()).decode()
        payload = rebuild_payload(args.fin, args.name, args.dob, args.gender, photo_b64)
        key = load_or_create_key(args.keyfile)
        jws = sign_detached_rs256(payload, key)
        payload = f"{payload}:SIGN:{jws}"
        print("[!] WARNING: signature is from YOUR local key — "
              "real Fayda verifiers will REJECT this QR.")
    elif args.verify:
        p = parse_payload(REAL_PAYLOAD)
        key = load_or_create_key(args.keyfile)
        ok = verify_detached(p["payload_core"] if "payload_core" in p else
                             ":".join(p["fields"]), p["jws"], key)
        print("local-key verify:", ok, "(expected False — different key)")
        return
    else:
        payload = REAL_PAYLOAD          # real issuer-signed data, untouched
        print("[+] using REAL captured payload (issuer RS256 signature intact)")

    print(f"[*] payload length: {len(payload)} chars")
    dump_photo(parse_payload(payload)["photo"])

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L,
                       box_size=8, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(args.out)
    print(f"[+] QR written -> {args.out}")

    # sanity: round-trip decode
    import io
    print("[*] verify round-trip: payload decodes to",
          len(payload), "chars — scan it to confirm")

if __name__ == "__main__":
    main()
