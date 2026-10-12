from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np
from pathlib import Path
from datetime import date
from io import BytesIO

from app.config import BASE_DIR
from core.image.image_bg_remove import get_image_without_bg

# ======================
# 🔹 Constants and Paths
# ======================
FONT_AMHARIC_DEFAULT = "/usr/share/fonts/truetype/sil-abyssinica/AbyssinicaSIL-Regular.ttf"
FONT_ENGLISH_DEFAULT = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"

TEMPLATES_DIR = BASE_DIR / "data" / "templates"
TEMPLATE_B_PATH = TEMPLATES_DIR / "template_black_cur.png"

# --- Output quality -------------------------------------------------------
# Same scheme as image_generator.py (Template A): authored at 1280x390 ("1x"),
# shipped at OUTPUT_SCALE x that, drawn on a SUPERSAMPLE x bigger canvas and
# LANCZOS-reduced down. Field coords below stay in 1x units.
OUTPUT_SCALE = 3          # final image = 3840x1170
SUPERSAMPLE = 2           # draw canvas = 7680x2340

# User provided coordinates for Template Black Cur (1280 x 390)
TEMPLATE_B_FIELDS = {
    # Amharic Fields
    "name_am": {"type": "text", "coords": (246, 115), "lang": "am", "size": 19},
    
    # English / Numeric / Combined Fields
    "name_en": {"type": "text", "coords": (246, 140), "lang": "en", "size": 19},
    "date_of_birth_et": {"type": "text", "coords": (246, 190), "lang": "am"}, # Mapping DOB Greg/Eth combo here
    "sex_en": {"type": "text", "coords": (246, 232), "lang": "am"},
    "expiry_date": {"type": "text", "coords": (246, 272), "lang": "am"},
    "phone_number": {"type": "text", "coords": (688, 45), "lang": "en"},
    "nationality": {"type": "text", "coords": (688, 100), "lang": "am"}, 
    
    # Address Fields - Coordinates provided by user
    "region_am": {"type": "text", "coords": (688, 140), "lang": "am", "size": 17},
    "region_en": {"type": "text", "coords": (688, 160), "lang": "am", "size": 17},
    "zone_am": {"type": "text", "coords": (688, 180), "lang": "am", "size": 17},
    "zone_en": {"type": "text", "coords": (688, 200), "lang": "am", "size": 17},
    "woreda_am": {"type": "text", "coords": (688, 220), "lang": "am", "size": 17},
    "woreda_en": {"type": "text", "coords": (688, 240), "lang": "am", "size": 17},
    
    "fin_code": {"type": "text", "coords": (767, 311), "lang": "en", "size": 17},
    # FAN number in the TOP band of the pill; barcode drawn below it (no overlap).
    "fan_code": {"type": "text", "coords": (290, 313), "lang": "en", "size": 16},
    # Image fields (x1, y1, x2, y2)
    "photo": {"type": "image", "coords": (25, 75, 240, 360)},
    "barcode": {"type": "image", "coords": (277, 332, 458, 370)},
    "small_image": {"type": "image", "coords": (500, 300, 570, 375)},
    "qrcode": {"type": "image", "coords": (940, 15, 1258, 338)},
}

# template_black_cur.png ships with a baked-in SAMPLE FIN ("7402 5903 1650") in the
# white pill after the "ልዩ ፋይዳ ቁጥር | FIN" label. Paint just that number out so the
# real FIN can be drawn in its place. Original (pre-supersample) pixel coords.
FIN_SAMPLE_COVER = (764, 310, 914, 337)
FIN_PILL_COLOR = (254, 254, 253)

# The barcode pill also carries a baked-in SAMPLE FAN number ("4952 ...") in its TOP
# band. Paint it out (pure white pill here) so the real FAN (fan_code text field)
# replaces it instead of overlapping. Original (pre-supersample) pixel coords.
FAN_SAMPLE_COVER = (277, 313, 455, 335)
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

    One native stroke pass instead of the old (boldness*scale)^2 offset stamps.
    A stroke grows both sides, so we use half the old total growth and shift the
    origin by the same amount to keep the ink centred where it used to be.
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

    Drawn as one block (not in the generic text loop) because the loop visits
    fields in dict order — woreda_am comes *before* zone_en — so it can't move
    woreda down once zone_en turns out to be long. Here we walk the lines in true
    top-to-bottom order and carry a running `shift`.
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

def draw_vertical_text(base_img, position, text, font_path, font_size=20, fill=(0, 0, 0), boldness=1, scale=1):
    try:
        font = ImageFont.truetype(font_path, font_size * scale)
    except Exception as e:
        print(f"[Warning] Failed to load vertical text font: {e}")
        font = ImageFont.load_default()

    text_img = Image.new("RGBA", (500 * scale, 100 * scale), (255, 255, 255, 0))
    text_draw = ImageDraw.Draw(text_img)

    # One stroke pass instead of the old (boldness*scale)^2 offset stamps.
    stroke = round(boldness * scale / 2)
    text_draw.text(
        (stroke, stroke), text, font=font, fill=fill,
        stroke_width=stroke, stroke_fill=fill,
    )

    rotated = text_img.rotate(90, expand=True)
    x, y = position
    x *= scale
    y *= scale
    base_img.paste(rotated, (x, y - rotated.height), rotated)

# ======================
# 🔹 Main Function
# ======================
def generate_final_id_image_b(
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

    # Process photo
    raw_photo = photo
    processed_photo = None
    if raw_photo is not None:
        try:
            processed_photo = get_image_without_bg(raw_photo)
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


    # 2️⃣ Load template Black Cur
    template_img = cv2.imread(str(TEMPLATE_B_PATH))
    if template_img is None:
        raise FileNotFoundError(f"Template Black Cur not found at {TEMPLATE_B_PATH}")
    img_pil = Image.fromarray(cv2.cvtColor(template_img, cv2.COLOR_BGR2RGB))

    # 3️⃣ Draw on a supersampled canvas, then reduce to OUTPUT_SCALE x.
    scale = OUTPUT_SCALE * SUPERSAMPLE
    w, h = img_pil.size
    img_large = img_pil.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
    draw_large = ImageDraw.Draw(img_large)

    # Erase the template's baked-in sample FIN number before drawing the real one.
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
        font_en_large = ImageFont.truetype(font_english, font_size * scale)
    except:
        font_am_large = ImageFont.load_default()
        font_en_large = ImageFont.load_default()

    # 4️⃣ Format text data
    today = date.today()
    e_year, e_month, e_day = gregorian_to_ethiopian(today.year, today.month, today.day)
    date_of_issue_greg = f"{today.year}/{today.month:02d}/{today.day:02d}"
    date_of_issue_eth = f"{e_day:02d}/{e_month:02d}/{e_year}"
    expiry_eth_date = f"{e_day:02d}/{e_month:02d}/{e_year + 8}"
    expiry_date_greg = f"{today.year + 8}/{today.month:02d}/{today.day:02d}"
    
    text_data["expiry_date"] = f"{expiry_eth_date} | {expiry_date_greg}"
    text_data["nationality"] = "ኢትዮጵያዊ | Ethiopian"
    
    # Combine address fields
    address_am = f"{text_data.get('region_am', '')}, {text_data.get('zone_am', '')}, {text_data.get('woreda_am', '')}"
    text_data["address"] = address_am

    # 5️⃣ Draw text fields
    for key, field in TEMPLATE_B_FIELDS.items():
        if field["type"] != "text" or key not in text_data:
            continue

        # The address lines (region/zone/woreda) are drawn together below so the
        # zone can wrap and shift woreda down; skip them in this generic pass.
        if key in ADDRESS_KEYS:
            continue

        text_to_draw = str(text_data[key])
        
        # Determine font size for this field
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

        if key == "date_of_birth_et":
            dob_greg = text_data.get('date_of_birth_greg', '')
            # Try to reformat dd/mm/yyyy to yyyy/mm/dd
            if "/" in dob_greg:
                parts = dob_greg.split("/")
                if len(parts) == 3 and len(parts[2]) == 4: # dd/mm/yyyy
                    dob_greg = f"{parts[2]}/{parts[1]}/{parts[0]}"
            
            text_to_draw = f"{text_data.get('date_of_birth_et', '')} | {dob_greg}"
        elif key == "sex_en":
            text_to_draw = f"{text_data.get('sex_am', '')} | {text_data.get('sex_en', '')}"
        elif key == "fan_code":
            text_to_draw = " ".join([text_to_draw[i:i+4] for i in range(0, len(text_to_draw), 4)])

        draw_bold_text(draw_large, (x, y), text_to_draw, font_use, boldness=boldness, scale=scale)

    # Address block (region / zone / woreda). Drawn after the loop so a long zone
    # wraps onto extra lines and pushes woreda down, instead of running into the QR.
    draw_address_block(
        draw_large, text_data, TEMPLATE_B_FIELDS, scale, boldness,
        font_amharic, font_english, font_size,
    )

    # 6️⃣ Paste images
    for key, field in TEMPLATE_B_FIELDS.items():
        if field["type"] != "image" or key not in image_crops:
            continue
        crop_img = image_crops[key]
        if crop_img is None: continue

        try:
            pil_crop = None
            if key == "photo" or key == "small_image":
                pil_crop = crop_img
            else:
                if isinstance(crop_img, np.ndarray):
                    if crop_img.size == 0: continue
                    pil_crop = Image.fromarray(cv2.cvtColor(crop_img, cv2.COLOR_BGR2RGB))
                else:
                    pil_crop = crop_img.convert("RGBA")

            if pil_crop is None: continue

            x1, y1, x2, y2 = field["coords"]
            target_w, target_h = (x2 - x1) * scale, (y2 - y1) * scale
            pil_crop = pil_crop.resize((target_w, target_h), Image.Resampling.LANCZOS)

            if pil_crop.mode == "RGBA":
                img_large.paste(pil_crop, (x1 * scale, y1 * scale), pil_crop)
            else:
                img_large.paste(pil_crop, (x1 * scale, y1 * scale))
        except Exception:
            pass

    # 7️⃣ Draw vertical text (Consistent with Amharic font)
    draw_vertical_text(img_large, (5, 165), date_of_issue_greg, font_amharic, 14, boldness=boldness, scale=scale)
    draw_vertical_text(img_large, (5, 325), date_of_issue_eth, font_amharic, 14, boldness=boldness, scale=scale)


    # 8️⃣ Reduce the supersampled canvas down to the final output size.
    img_final = img_large.resize(
        (w * OUTPUT_SCALE, h * OUTPUT_SCALE), Image.Resampling.LANCZOS
    )

    # 9️⃣ Return as high-quality PNG bytes (match Template A's 300 dpi tag)
    buffer = BytesIO()
    img_final.save(buffer, format="PNG", compress_level=6, dpi=(300, 300))
    return buffer.getvalue()
