"""Shared text utilities for Telegram and PDF renderers."""


def _clamp(text: str, max_chars: int, by: str = "sentence") -> str:
    """Smart text truncation that never cuts mid-word.

    Args:
        text: Input string.
        max_chars: Maximum allowed length.
        by: Strategy — 'sentence' (first sentence boundary),
            'colon' (before first ':'), 'word' (last word boundary).
    """
    if not text:
        return text
    # colon mode: always strip to first colon (name-only extraction)
    if by == "colon" and ":" in text:
        before = text[: text.index(":")].strip()
        if before:
            return before
    if len(text) <= max_chars:
        return text
    if by == "sentence":
        for sep in [". ", "; ", ", "]:
            idx = text.rfind(sep, 0, max_chars)
            if idx > max_chars // 3:
                return text[: idx + 1].strip()
    # fallback: word boundary
    idx = text.rfind(" ", 0, max_chars)
    return text[:idx].strip() if idx > 0 else text[:max_chars].strip()
