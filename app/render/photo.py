"""Player photos: any WebP/JPEG/PNG in, a 256x256 WebP out with no metadata."""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
SIZE = 256
# Refuse anything larger before decoding a single pixel (image bombs).
MAX_PIXELS = 40_000_000
TARGET_BYTES = 60_000
ALLOWED_FORMATS = frozenset({"WEBP", "JPEG", "PNG"})
ALLOWED_TYPES = frozenset({"image/webp", "image/jpeg", "image/png"})
_QUALITIES = (82, 70, 58, 46, 34)


class UnsupportedImageError(Exception):
    """Not a WebP, JPEG or PNG we are willing to decode."""


def _square(image: Image.Image) -> Image.Image:
    upright = ImageOps.exif_transpose(image)
    has_alpha = upright.mode in ("RGBA", "LA", "PA") or "transparency" in upright.info
    converted = upright.convert("RGBA" if has_alpha else "RGB")
    return ImageOps.fit(converted, (SIZE, SIZE), Image.Resampling.LANCZOS)


def reencode(data: bytes) -> bytes:
    """A fresh 256x256 WebP (at most ~60 KB): EXIF, GPS and every other chunk are dropped."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ALLOWED_FORMATS:
                    raise UnsupportedImageError(f"format {image.format}")
                if image.width * image.height > MAX_PIXELS:
                    raise UnsupportedImageError("too many pixels")
                image.draft("RGB", (SIZE * 2, SIZE * 2))
                square = _square(image)
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            ValueError,
        ) as exc:
            raise UnsupportedImageError(str(exc)) from exc
    encoded = b""
    for quality in _QUALITIES:
        out = io.BytesIO()
        square.save(out, format="WEBP", quality=quality, method=6)
        encoded = out.getvalue()
        if len(encoded) <= TARGET_BYTES:
            break
    return encoded
