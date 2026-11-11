"""String helpers that behave like the app's JavaScript string methods."""

import unicodedata

# JavaScript's String.prototype.trim set (WhiteSpace + LineTerminator); Python's strip() differs slightly.
_JS_WHITESPACE = "".join(
    map(chr, [*range(0x9, 0xE), 0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029])
) + "".join(map(chr, [0x202F, 0x205F, 0x3000, 0xFEFF]))


def js_trim(s: str) -> str:
    """`s.trim()` in JavaScript."""
    return s.strip(_JS_WHITESPACE)


def nfc(s: str) -> str:
    """`s.normalize('NFC')`."""
    return unicodedata.normalize("NFC", s)


def utf16_len(s: str) -> int:
    """`s.length` in JavaScript: UTF-16 code units (equals len() for BMP text)."""
    return len(s.encode("utf-16-le")) // 2
