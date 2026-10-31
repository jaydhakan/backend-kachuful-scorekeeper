"""QR codes as SVG (segno)."""

import io

import segno


def qr_svg(url: str) -> str:
    """A scalable black-on-white QR code for a URL."""
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="svg", scale=8, border=2, xmldecl=False)
    return buffer.getvalue().decode()
