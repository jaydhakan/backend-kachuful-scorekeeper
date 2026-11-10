"""Clue checks (app: clue.ts): the format of a typed clue and the words it may not echo."""

import unicodedata
from typing import Any

from app.domain.ishaara.text import js_trim, nfc, utf16_len

MAX_CLUE_LEN = 24
_SUFFIXES = ("s", "es", "ed", "ing", "er")
# A face-down word inside a longer clue counts only from this length, so "pin" doesn't block "spine".
_MIN_PART = 3


def norm(s: str) -> str:
    """Lower-case, composed form: how clues and board words are compared."""
    return js_trim(nfc(s).lower())


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("L", "M")


def clue_format_ok(word: str) -> bool:
    """One word: letters, with single hyphens between parts."""
    w = norm(word)
    if not w or len(w) > MAX_CLUE_LEN:
        return False
    return all(bool(part) and all(_is_letter(ch) for ch in part) for part in w.split("-"))


def forms_of(w: str) -> list[str]:
    """The simple forms of a word: -s, -es, -ed, -ing, -er, and the same after a silent e."""
    out = [w + s for s in _SUFFIXES]
    if w.endswith("e"):
        out += [w + "d", w + "r", w[:-1] + "ing"]
    return out


def _is_form_of(a: str, b: str) -> bool:
    return a in forms_of(b)


def _clash(clue: str, word: str) -> str | None:
    if clue == word:
        return "same"
    if _is_form_of(clue, word) or _is_form_of(word, clue):
        return "form"
    parts = clue.split("-")
    if len(parts) > 1 and any(p == word or _is_form_of(p, word) for p in parts):
        return "part"
    # The app compares UTF-16 lengths here; utf16_len keeps that exact outside the BMP too.
    longer = utf16_len(word) >= _MIN_PART and utf16_len(clue) > utf16_len(word)
    if longer and (clue.startswith(word) or clue.endswith(word)):
        return "part"
    return None


def clue_allowed(word: str, state: dict[str, Any]) -> dict[str, Any]:
    """Checks a typed clue against the words still face down (the app's ClueCheck shape)."""
    clue = norm(word)
    if not clue:
        return {"ok": False, "reason": "empty"}
    if not clue_format_ok(clue):
        return {"ok": False, "reason": "format"}
    for i, board_word in enumerate(state["words"]):
        if state["revealed"][i] is not None:
            continue
        reason = _clash(clue, norm(board_word))
        if reason:
            return {"ok": False, "reason": reason, "word": board_word}
    return {"ok": True}


def as_int(n: object) -> int | None:
    """An integral JSON number as an int (JS has one number type, so 2.0 is 2); None otherwise."""
    if isinstance(n, bool):
        return None
    if isinstance(n, int):
        return n
    if isinstance(n, float) and n.is_integer():
        return int(n)
    return None


def clue_number(n: object) -> int | str | None:
    """A valid clue number (0-9 or 'inf'), normalised; None when it isn't one."""
    if n == "inf":
        return "inf"
    i = as_int(n)
    return i if i is not None and 0 <= i <= 9 else None


def guesses_for(n: int | str) -> int | str:
    """Clue 1-9 allows number + 1 guesses; 0 and 'inf' allow as many as the team wants."""
    if n == "inf" or n == 0:
        return "inf"
    assert isinstance(n, int)
    return n + 1
