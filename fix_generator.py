import pathlib

base = pathlib.Path('/home/ramsi/Desktop/projects/fayda_bot/core/image')
file_a = base / 'image_generator.py'
file_b = base / 'image_generator_b.py'

# ---- Patch Template A ----
content_a = file_a.read_text()
# 1. Size fix
content_a = content_a.replace(
    '"fan_code": {"type": "text", "coords": (281, 294), "lang": "en", "size": 16}',
    '"fan_code": {"type": "text", "coords": (281, 294), "lang": "en", "size": 14}'
)
# 2. Dynamic font load fix
old_font = """        text_to_draw = str(text_data[key])
        font_use = font_am_large if field.get("lang") == "am" else font_en_large
        x, y = field["coords"]"""
new_font = """        text_to_draw = str(text_data[key])
        
        current_font_size = field.get("size", font_size)
        try:
            f_am = ImageFont.truetype(font_amharic, current_font_size * scale)
            f_en = ImageFont.truetype(font_english, current_font_size * scale)
        except:
            f_am = font_am_large
            f_en = font_en_large
            
        font_use = f_am if field.get("lang") == "am" else f_en
        
        x, y = field["coords"]"""
content_a = content_a.replace(old_font, new_font)

# 3. Space formatting fix
old_space = """        elif key == "date_of_birth_et" and "date_of_birth_greg" in text_data:
            text_to_draw = f"{text_data['date_of_birth_et']} | {text_data['date_of_birth_greg']}"
            font_use = font_en_large

        draw_bold_text(draw_large, (x, y), text_to_draw, font_use, boldness=boldness * scale)"""
new_space = """        elif key == "date_of_birth_et" and "date_of_birth_greg" in text_data:
            text_to_draw = f"{text_data['date_of_birth_et']} | {text_data['date_of_birth_greg']}"
            font_use = font_en_large
        elif key == "fan_code":
            text_to_draw = " ".join([text_to_draw[i:i+4] for i in range(0, len(text_to_draw), 4)])

        draw_bold_text(draw_large, (x, y), text_to_draw, font_use, boldness=boldness * scale)"""
content_a = content_a.replace(old_space, new_space)

file_a.write_text(content_a)


# ---- Patch Template B ----
content_b = file_b.read_text()
# 1. Size fix
content_b = content_b.replace(
    '"fan_code": {"type": "text", "coords": (285, 313), "lang": "en", "size": 16}',
    '"fan_code": {"type": "text", "coords": (285, 313), "lang": "en", "size": 14}'
)
# 2. Space formatting fix
old_space_b = """        elif key == "sex_en":
            text_to_draw = f"{text_data.get('sex_am', '')} | {text_data.get('sex_en', '')}"

        draw_bold_text(draw_large, (x, y), text_to_draw, font_use, boldness=boldness * scale)"""
new_space_b = """        elif key == "sex_en":
            text_to_draw = f"{text_data.get('sex_am', '')} | {text_data.get('sex_en', '')}"
        elif key == "fan_code":
            text_to_draw = " ".join([text_to_draw[i:i+4] for i in range(0, len(text_to_draw), 4)])

        draw_bold_text(draw_large, (x, y), text_to_draw, font_use, boldness=boldness * scale)"""
content_b = content_b.replace(old_space_b, new_space_b)

file_b.write_text(content_b)

print("Files patched!")
