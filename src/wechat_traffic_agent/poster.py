"""Render a 题图 draft as a poster PNG for WeCom image delivery."""
from __future__ import annotations

from pathlib import Path

from .models import TietuDraft

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


def render_poster_png(draft: TietuDraft, path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 800, 1100
    img = Image.new("RGB", (width, height), (28, 28, 30))
    draw = ImageDraw.Draw(img)
    title_font = _font(64)
    line_font = _font(44)
    small_font = _font(30)

    def centered(text: str, y: int, font, fill=(245, 245, 245)) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        x = (width - (box[2] - box[0])) // 2
        draw.text((x, y), text, font=font, fill=fill)
        return y + (box[3] - box[1]) + 24

    y = 120
    y = centered(draft.title, y, title_font, fill=(255, 214, 102))
    y += 40
    for line in draft.lines:
        y = centered(line, y, line_font)
    y += 60
    y = centered(draft.interaction_hook, y, small_font, fill=(180, 180, 190))
    y += 50
    centered(f"· {draft.angle.value} ·", y, small_font, fill=(120, 120, 130))

    img.save(path)
    return path


def _font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()
