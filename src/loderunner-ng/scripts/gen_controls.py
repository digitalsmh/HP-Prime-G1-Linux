#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lode Runner NG — HP Prime G1 320x240 Controls Splash Generator
"""

import os
from PIL import Image, ImageDraw, ImageFont

def generate_controls_image(output_path):
    w, h = 320, 240
    img = Image.new('RGB', (w, h), color=(12, 14, 24))
    draw = ImageDraw.Draw(img)

    # Outer decorative border
    draw.rectangle([(2, 2), (w - 3, h - 3)], outline=(60, 90, 150), width=1)
    draw.rectangle([(4, 4), (w - 5, h - 5)], outline=(120, 160, 240), width=1)

    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    font_title = ImageFont.truetype(font_path, 15, index=0)
    font_sub = ImageFont.truetype(font_path, 11, index=0)
    font_key = ImageFont.truetype(font_path, 11, index=0)
    font_desc = ImageFont.truetype(font_path, 11, index=0)
    font_foot = ImageFont.truetype(font_path, 10, index=0)

    # Title & Subtitle
    draw.text((w // 2, 16), "LODE RUNNER", fill=(255, 215, 0), font=font_title, anchor="mm")
    draw.text((w // 2, 33), "- HP Prime Key Controls -", fill=(100, 210, 255), font=font_sub, anchor="mm")
    draw.line([(15, 45), (w - 15, 45)], fill=(60, 90, 150), width=1)

    items = [
        ("방향키 / Touch", "이동 (사다리/봉 타기)", (255, 255, 255)),
        ("F 키", "좌측 땅 파기 (Dig Left)", (100, 255, 120)),
        ("G 키", "우측 땅 파기 (Dig Right)", (100, 255, 120)),
        ("P 키", "일시 정지 / 재개 (Pause)", (255, 230, 100)),
        ("DEL / Backspace", "자살 / 레벨 재시작", (255, 130, 130)),
        ("Q 키", "게임 완전 종료 (Quit)", (255, 100, 100)),
    ]

    y = 57
    for key_name, desc, color in items:
        draw.rectangle([(16, y - 2), (122, y + 16)], fill=(30, 36, 54), outline=(70, 90, 130))
        draw.text((69, y + 7), key_name, fill=color, font=font_key, anchor="mm")
        draw.text((132, y + 7), desc, fill=(225, 230, 240), font=font_desc, anchor="lm")
        y += 24

    draw.line([(15, y + 2), (w - 15, y + 2)], fill=(60, 90, 150), width=1)
    draw.rectangle([(20, y + 7), (w - 20, y + 25)], fill=(20, 28, 48), outline=(60, 130, 200))
    draw.text((w // 2, y + 16), "아무 키나 화면 터치 시 게임 시작 (1~5초)", fill=(255, 255, 140), font=font_foot, anchor="mm")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img.save(output_path)
    print(f"Generated controls splash: {output_path}")

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "../share/loderunner-ng/textures/controls.png")
    generate_controls_image(out)

