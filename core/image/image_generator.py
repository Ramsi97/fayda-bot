from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np
from pathlib import Path
from datetime import date
from io import BytesIO

from app.config import BASE_DIR
from core.image.image_black_and_white_conv import get_grayscale_image
from core.image.image_bg_remove import get_image_without_bg
# ======================
# 🔹 Constants and Paths
# ======================
FONT_AMHARIC_DEFAULT = "/usr/share/fonts/truetype/sil-abyssinica/AbyssinicaSIL-Regular.ttf"
FONT_ENGLISH_DEFAULT = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"

TEMPLATES_DIR = BASE_DIR / "data" / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "white_template_cur.png"

# --- Output quality -------------------------------------------------------
# The card is authored at 1280x390 (i.e. "1x"). We ship at OUTPUT_SCALE x that,
# so text / QR / barcode stay crisp when printed or zoomed. Every field coord in
# TEMPLATE_FIELDS below stays in 1x units and is multiplied up at draw time.
OUTPUT_SCALE = 3          # final image = 3840x1170
# Drawing happens on a canvas that is SUPERSAMPLE x bigger than the output, then
# gets LANCZOS-reduced down to OUTPUT_SCALE. That reduction is what anti-aliases
# the glyph edges. Must be >= 1; higher = smoother but more RAM/time.
SUPERSAMPLE = 2           # draw canvas = 7680x2340

TEMPLATE_FIELDS = {
    # Amharic Fields
    "name_am": {"type": "text", "coords": (242, 112), "lang": "am", "size": 19},
    "date_of_birth_et": {"type": "text", "coords": (242, 185), "lang": "am", "size": 17},
    "sex_am": {"type": "text", "coords": (242, 220), "lang": "am", "size": 17},
    "region_am": {"type": "text", "coords": (698, 144), "lang": "am", "size": 17},
    "zone_am": {"type": "text", "coords": (698, 186), "lang": "am", "size": 17},
    "woreda_am": {"type": "text", "coords": (698, 233), "lang": "am", "size": 17},

    # English / Numeric Fields
    "name_en": {"type": "text", "coords": (242, 138), "lang": "en", "size": 19},
    "date_of_birth_greg": {"type": "text", "coords": (242, 205), "lang": "am", "size": 17},
    "sex_en": {"type": "text", "coords": (314, 220), "lang": "am", "size": 17},
    "expiry_date": {"type": "text", "coords": (242, 256), "lang": "am", "size": 17},
    "phone_number": {"type": "text", "coords": (698, 50), "lang": "en", "size": 17},
    "nationality": {"type": "text", "coords": (698, 100), "lang": "am", "size": 17},
    "region_en": {"type": "text", "coords": (698, 165), "lang": "am", "size": 17},
    "zone_en": {"type": "text", "coords": (698, 209), "lang": "am", "size": 17},
    "woreda_en": {"type": "text", "coords": (698, 254), "lang": "am", "size": 17},
    # FAN number sits in the TOP band of the white pill; the barcode is drawn
    # in the lower band so the two don't overlap (matches the real card layout).
    "fan_code": {"type": "text", "coords": (286, 294), "lang": "en", "size": 14},

    "fin_code": {"type": "text", "coords": (762, 313), "lang": "en", "size": 17},
    "photo": {"type": "image", "coords": (25, 80, 230, 363)},
    "qrcode": {"type": "image", "coords": (943, 25, 1265, 338)},
    "small_image": {"type": "image", "coords": (501, 295, 560, 354)},
    "barcode": {"type": "image", "coords": (273, 311, 450, 345)},
}

# The template ships with a baked-in SAMPLE FIN ("4980 9389 5632") printed inside
# the white pill next to the "ፋይዳ ቁጥር | FIN" label. We paint just that number out
# (with the pill's own colour) so the real FIN can be drawn in its place without
# the two overlapping. Coords are original (pre-supersample) pixels, measured from
# white_template_cur.png. The "ፋይዳ ቁጥር | FIN" label is left intact.
FIN_SAMPLE_COVER = (759, 315, 887, 335)
FIN_PILL_COLOR = (254, 254, 253)

# The barcode pill also ships with a baked-in SAMPLE FAN number ("2875 ...") in its
# TOP band (above the sample barcode). Paint it out (the pill is pure white here) so
# the real FAN — drawn by the fan_code text field — replaces it instead of
# overlapping. Coords are original (pre-supersample) pixels from white_template_cur.png.
FAN_SAMPLE_COVER = (273, 294, 450, 315)
FAN_PILL_COLOR = (255, 255, 255)

# The address (region → zone → woreda) is drawn as a stacked block by
# draw_address_block instead of in the generic text loop, so the zone line can
# wrap when it's too wide (e.g. "Adama City Administration - Dabe") instead of
# running into the QR code. Only the zone wraps; region and woreda stay single
# lines and simply shift down to make room.
ADDRESS_KEYS = ("region_am", "region_en", "zone_am", "zone_en", "woreda_am", "woreda_en")
ADDRESS_WRAP_KEYS = ("zone_am", "zone_en")   # only the zone wraps when too wide
ADDRESS_QR_MARGIN = 10                        # 1x px kept between address text and QR

# ======================
# 🔹 Helper Functions
# ======================
def gregorian_to_ethiopian(g_y, g_m, g_d):
    ethiopian_month_lengths = [30] * 12 + [5]
    new_year_offset = 11
    g = date(g_y, g_m, g_d)
    e_new_year = date(g_y, 9, new_year_offset)
    if g < e_new_year:
        e_new_year = date(g_y - 1, 9, new_year_offset)
        e_year = g_y - 1 - 7
    else:
        e_year = g_y - 7

    delta = (g - e_new_year).days
    for m_idx, ml in enumerate(ethiopian_month_lengths):
        if delta < ml:
            return e_year, m_idx + 1, delta + 1
        delta -= ml
    return e_year, 13, delta + 1


def draw_bold_text(draw, position, text, font, fill=(0, 0, 0), boldness=1, scale=1):
    """Draw text thickened by `boldness` (in 1x units) on a `scale`x canvas.

    Shape matches the old offset-stamp loop: that loop drew copies at
    (0..boldness*scale)^2, i.e. a total outward growth of boldness*scale px. A
    stroke grows both sides, so we use half of it and shift the origin by the
    same amount to keep the ink centred where it used to be.
    """
    t = boldness * scale
    stroke = round(t / 2)
    if stroke > 0:
        x, y = position
        position = (x + stroke, y + stroke)
    draw.text(
        position, text, font=font, fill=fill,
        stroke_width=stroke, stroke_fill=fill,
    )


def wrap_text(draw, text, font, max_width):
    """Split `text` into a list of lines that each fit within `max_width` pixels.

    Words are split on spaces. If a single word is wider than max_width it is
    placed on its own line (it will overflow, but that's the best we can do).
    """
    words = text.split()
    if not words:
        return [text]

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = current_line + " " + word
        if draw.textlength(test_line, font=font) <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def draw_address_block(draw, text_data, fields, scale, boldness,
                       font_amharic, font_english, default_size):
    """Draw region / zone / woreda as a vertical stack in the address area.

    region and woreda stay single lines at their template Y. Only the zone lines
    wrap when wider than the gap to the QR code; each extra wrapped line pushes
    everything below it down by one line, so a long zone never collides with the
    woreda beneath it or runs into the QR.

    This is drawn as one block (not in the generic text loop) because the loop
    visits fields in dict order — woreda_am comes *before* zone_en — so the loop
    can't move woreda down once zone_en turns out to be long. Here we walk the
    lines in true top-to-bottom order and carry a running `shift`.
    """
    qr_left = fields["qrcode"]["coords"][0]
    addr_x = fields["zone_am"]["coords"][0]
    max_width = (qr_left - addr_x - ADDRESS_QR_MARGIN) * scale
    # Continuation lines reuse the zone's own am->en gap as their line height.
    line_h = (fields["zone_en"]["coords"][1] - fields["zone_am"]["coords"][1]) * scale

    shift = 0
    for key in ADDRESS_KEYS:
        field = fields.get(key)
        if not field or key not in text_data:
            continue
        text = str(text_data[key]).strip()
        if not text:
            continue

        size = field.get("size", default_size)
        font_path = font_amharic if field.get("lang") == "am" else font_english
        try:
            font = ImageFont.truetype(font_path, size * scale)
        except Exception:
            font = ImageFont.load_default()

        x = field["coords"][0] * scale
        y = field["coords"][1] * scale + shift

        lines = wrap_text(draw, text, font, max_width) if key in ADDRESS_WRAP_KEYS else [text]
        for i, line in enumerate(lines):
            draw_bold_text(draw, (x, y + i * line_h), line, font,
                           boldness=boldness, scale=scale)
        if len(lines) > 1:
            shift += (len(lines) - 1) * line_h


def draw_vertical_text(base_img, position, text, font_path, font_size=22, fill=(0, 0, 0), boldness=1, scale=1):
    """Draw sharp vertical text (rotated upward) using supersampling."""
    try:
        font = ImageFont.truetype(font_path, font_size * scale)
    except Exception:
        font = ImageFont.load_default()

    # Make a transparent canvas for the text
    text_img = Image.new("RGBA", (500 * scale, 100 * scale), (255, 255, 255, 0))
    text_draw = ImageDraw.Draw(text_img)

    # One stroke pass instead of the old (boldness*scale)^2 offset stamps.
    # Half-width stroke (matches the old total growth) with the origin shifted
    # by the same amount, exactly as draw_bold_text does.
    stroke = round(boldness * scale / 2)
    text_draw.text(
        (stroke, stroke), text, font=font, fill=fill,
        stroke_width=stroke, stroke_fill=fill,
    )

    # Rotate upward
    rotated = text_img.rotate(90, expand=True)

    # Paste upward relative to the position
    x, y = position
    x *= scale
    y *= scale
    base_img.paste(rotated, (x, y - rotated.height), rotated)


# ======================
# 🔹 Main Function
# ======================
def generate_final_id_image(
    text_data: dict,
    photo=None,
    qrcode_img=None,
    barcode_img=None,
    font_amharic: str = FONT_AMHARIC_DEFAULT,
    font_english: str = FONT_ENGLISH_DEFAULT,
    font_size: int = 17,
    boldness: float = 0.5,
    color: bool = True,
) -> bytes:
    """Generate a final sharp ID image (PNG bytes) from API-provided data."""
    if text_data is None:
        raise ValueError("text_data must be provided")

    image_crops = {
        "barcode": barcode_img,
        "qrcode": qrcode_img,
    }


    # --- OPTIMIZATION: Process photo once and reuse ---
    raw_photo = photo
    processed_photo = None
    if raw_photo is not None:
        try:
            # Remove BG ONCE
            processed_photo = get_image_without_bg(raw_photo)
            
            # Apply grayscale if needed (common for both)
            if not color:
                alpha = processed_photo.getchannel('A')
                processed_photo = processed_photo.convert('L').convert('RGBA')
                processed_photo.putalpha(alpha)
        except Exception:
            if isinstance(raw_photo, np.ndarray):
                processed_photo = Image.fromarray(cv2.cvtColor(raw_photo, cv2.COLOR_BGR2RGB)).convert("RGBA")
            else:
                processed_photo = raw_photo.convert("RGBA")

    image_crops["photo"] = processed_photo
    image_crops["small_image"] = processed_photo

    
    # 2️⃣ Load base template
    template_img = cv2.imread(str(TEMPLATE_PATH))
    if template_img is None:
        raise FileNotFoundError(f"Template not found at {TEMPLATE_PATH}")
    img_pil = Image.fromarray(cv2.cvtColor(template_img, cv2.COLOR_BGR2RGB))

    # 3️⃣ Draw on a supersampled canvas, then reduce to OUTPUT_SCALE x.
    #    scale = OUTPUT_SCALE * SUPERSAMPLE (e.g. 3*2 = 6 -> 7680x2340 canvas).
    scale = OUTPUT_SCALE * SUPERSAMPLE
    w, h = img_pil.size
    img_large = img_pil.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
    draw_large = ImageDraw.Draw(img_large)

    # Erase the template's baked-in sample FIN number so the real FIN (drawn in
    # the text loop below) sits cleanly in the pill instead of overlapping it.
    cx1, cy1, cx2, cy2 = FIN_SAMPLE_COVER
    draw_large.rectangle(
        (cx1 * scale, cy1 * scale, cx2 * scale, cy2 * scale),
        fill=FIN_PILL_COLOR,
    )

    # Same for the baked-in sample FAN number in the barcode pill's top band.
    fx1, fy1, fx2, fy2 = FAN_SAMPLE_COVER
    draw_large.rectangle(
        (fx1 * scale, fy1 * scale, fx2 * scale, fy2 * scale),
        fill=FAN_PILL_COLOR,
    )

    # Load fonts
    try:
        font_am_large = ImageFont.truetype(font_amharic, font_size * scale)
    except Exception:
        font_am_large = ImageFont.load_default()
    try:
        font_en_large = ImageFont.truetype(font_english, font_size * scale)
    except Exception:
        font_en_large = font_am_large

    # 4️⃣ Generate date data
    today = date.today()
    e_year, e_month, e_day = gregorian_to_ethiopian(today.year, today.month, today.day)
    date_of_issue_greg = f"{today.day:02d}/{today.month:02d}/{today.year}"
    date_of_issue_eth = f"{e_day:02d}/{e_month:02d}/{e_year}"
    expiry_eth_date = f"{e_day:02d}/{e_month:02d}/{e_year + 8}"
    expiry_date_greg = f"{today.day:02d}/{today.month:02d}/{today.year + 8}"
    text_data["expiry_date"] = f"{expiry_eth_date} | {expiry_date_greg}"
    text_data["nationality"] = "ኢትዮጵያዊ | Ethiopian"

    # 5️⃣ Draw text fields
    for key, field in TEMPLATE_FIELDS.items():
        if field["type"] != "text" or key not in text_data:
            continue

        # The address lines (region/zone/woreda) are drawn together below so the
        # zone can wrap and shift woreda down; skip them in this generic pass.
        if key in ADDRESS_KEYS:
            continue

        text_to_draw = str(text_data[key])
        
        current_font_size = field.get("size", font_size)
        try:
            f_am = ImageFont.truetype(font_amharic, current_font_size * scale)
            f_en = ImageFont.truetype(font_english, current_font_size * scale)
        except:
            f_am = font_am_large
            f_en = font_en_large
            
        font_use = f_am if field.get("lang") == "am" else f_en
        
        x, y = field["coords"]
        x *= scale
        y *= scale

        # Handle combined or special fields
        if key == "sex_en":
            am_text = text_data.get("sex_am", "")
            am_width = draw_large.textlength(am_text, font=font_am_large)
            x = (TEMPLATE_FIELDS["sex_am"]["coords"][0] * scale) + am_width + 10
            text_to_draw = "| " + text_to_draw
        elif key == "date_of_birth_greg":
            continue
        elif key == "date_of_birth_et" and "date_of_birth_greg" in text_data:
            text_to_draw = f"{text_data['date_of_birth_et']} | {text_data['date_of_birth_greg']}"
            font_use = font_en_large
        elif key == "fan_code":
            text_to_draw = "  ".join([text_to_draw[i:i+4] for i in range(0, len(text_to_draw), 4)])

        draw_bold_text(draw_large, (x, y), text_to_draw, font_use, boldness=boldness, scale=scale)

    # Address block (region / zone / woreda). Drawn after the loop so a long zone
    # wraps onto extra lines and pushes woreda down, instead of running into the QR.
    draw_address_block(
        draw_large, text_data, TEMPLATE_FIELDS, scale, boldness,
        font_amharic, font_english, font_size,
    )

    # 6️⃣ Paste cropped images
    for key, field in TEMPLATE_FIELDS.items():
        if field["type"] != "image" or key not in image_crops:  
            continue
        crop_img = image_crops[key]
        if crop_img is None: # Photo could be None if missing in PDF
            continue
            
        try:
            pil_crop = None

            # --- 1. Use already processed photo/small_image OR process other images ---
            if key == "photo" or key == "small_image":
                # Already processed above!
                pil_crop = crop_img
            else:
                # For other image types (QR code, etc.), just convert to PIL
                if isinstance(crop_img, np.ndarray):
                    if crop_img.size == 0: continue
                    pil_crop = Image.fromarray(cv2.cvtColor(crop_img, cv2.COLOR_BGR2RGB))
                else:
                    pil_crop = crop_img.convert("RGBA")

            if pil_crop is None: continue

            # --- 2. RESIZE ---
            x1, y1, x2, y2 = field["coords"]
            target_w, target_h = (x2 - x1) * scale, (y2 - y1) * scale
            pil_crop = pil_crop.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # --- 3. PASTE ---
            if pil_crop.mode == "RGBA":
                # Use alpha as mask
                img_large.paste(pil_crop, (x1 * scale, y1 * scale), pil_crop)
            else:
                # No transparency (e.g., barcode)
                img_large.paste(pil_crop, (x1 * scale, y1 * scale))

        except Exception:
            pass

    # 7️⃣ Draw vertical date text (both) - Consistent with Amharic font size 14
    draw_vertical_text(img_large, (7, 156), date_of_issue_greg, font_amharic, 14, boldness=boldness, scale=scale)
    draw_vertical_text(img_large, (7, 310), date_of_issue_eth, font_amharic, 14, boldness=boldness, scale=scale)

    # 8️⃣ Reduce the supersampled canvas down to the final output size.
    #    (This step is what makes the text/QR edges smooth — don't skip it.)
    img_final = img_large.resize(
        (w * OUTPUT_SCALE, h * OUTPUT_SCALE), Image.Resampling.LANCZOS
    )

    # 9️⃣ Return as high-quality PNG bytes.
    #    compress_level=6 is zlib's default: same ~2% smaller file as
    #    optimize=True but without the exhaustive search that tripled save time.
    buffer = BytesIO()
    img_final.save(buffer, format="PNG", compress_level=6, dpi=(300, 300))
    return buffer.getvalue()
