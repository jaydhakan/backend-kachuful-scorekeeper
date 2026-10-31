"""The 1200x630 recap preview: title, top three with emoji and points, rounds and date."""

import io
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"
WIDTH, HEIGHT = 1200, 630
FELT = (6, 18, 13)
TABLE = (15, 59, 43)
TEXT = (245, 241, 232)
MUTED = (169, 184, 175)
GOLD = (233, 196, 106)
EMOJI_NATIVE = 109  # the only size the bitmap emoji font has


@dataclass(frozen=True)
class CardRow:
    """One of the top three."""

    place: int
    emoji: str
    name: str
    points: int
    photo: bytes | None = None


@cache
def _font(name: str, size: int, weight: bytes | None = None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        font.set_variation_by_name(weight)
    return font


def _emoji(char: str, size: int) -> Image.Image:
    tile = Image.new("RGBA", (EMOJI_NATIVE + 30, EMOJI_NATIVE + 30), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text(
        (0, 0), char, font=_font("NotoColorEmoji.ttf", EMOJI_NATIVE), embedded_color=True
    )
    box = tile.getbbox()
    if box is None:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return tile.crop(box).resize((size, size), Image.Resampling.LANCZOS)


def _avatar(row: CardRow, size: int) -> Image.Image:
    """The player's photo in a circle, or their emoji."""
    if row.photo is None:
        return _emoji(row.emoji or "🙂", size)
    with Image.open(io.BytesIO(row.photo)) as photo:
        square = photo.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    square.putalpha(mask)
    return square


def render_recap(title: str, rows: list[CardRow], subtitle: str) -> bytes:
    """PNG bytes for a recap card."""
    image = Image.new("RGB", (WIDTH, HEIGHT), FELT)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, WIDTH - 40, HEIGHT - 40), radius=48, fill=TABLE)
    draw.text((90, 80), title, font=_font("BricolageGrotesque.ttf", 64, b"ExtraBold"), fill=TEXT)
    draw.text((90, 165), subtitle, font=_font("DMSans.ttf", 32, b"Medium"), fill=MUTED)
    name_font = _font("BricolageGrotesque.ttf", 52, b"Bold")
    points_font = _font("DMSans.ttf", 48, b"Bold")
    for i, row in enumerate(rows[:3]):
        top = 240 + i * 92
        colour = GOLD if row.place == 1 else TEXT
        draw.text((90, top + 8), f"{row.place}", font=points_font, fill=MUTED)
        avatar = _avatar(row, 64)
        image.paste(avatar, (150, top), avatar)
        draw.text((240, top + 4), row.name, font=name_font, fill=colour)
        score = f"{row.points:+d}" if row.points else "0"
        width = draw.textlength(score, font=points_font)
        draw.text((WIDTH - 90 - width, top + 8), score, font=points_font, fill=colour)
    draw.text(
        (WIDTH - 360, HEIGHT - 100),
        "play.vectanix.com",
        font=_font("DMSans.ttf", 26, b"Regular"),
        fill=MUTED,
    )
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
