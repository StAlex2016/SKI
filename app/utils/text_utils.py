"""Shared text utilities for Telegram and PDF renderers."""
import re


# ── FRAME-REF SCRUBBING ────────────────────────────────────────────────────────
# The video-analysis LLM is prompted to do per-frame technical review, which
# leaks frame numbers ("кадр 3", "on frame 5", "в кадрах 2-4") into
# user-facing sections (strengths / growth areas / drills / phase obs / photo
# report text). The end user doesn't see the frames — they get a PDF — so
# these references are confusing. This helper is the single place that removes
# them, belt-and-suspenders alongside the prompt instruction to avoid them.

# Any parenthesised group mentioning frame/кадр (most common LLM form)
_PAREN_FRAME_RE = re.compile(
    r"\s*\([^)]*(?:кадр|frame)[^)]*\)", re.IGNORECASE
)
# Russian prepositional: "на кадрах 5-7", "в кадре 3", "по кадру", "из кадров 1,2"
_RU_PREP_RE = re.compile(
    r"\s*\b(?:на|во?|по|из|с|к|у|при|о|об|от|для)\s+кадр\w*"
    r"(?:\s+[№#]?\s*\d+(?:\s*[,\-\u2013]\s*\d+)*)?",
    re.IGNORECASE,
)
# English prepositional: "on frame 3", "in frames 5-7", "from frame 2", "see frame 4"
_EN_PREP_RE = re.compile(
    r"\s*\b(?:on|in|at|from|see|of|for)\s+frames?"
    r"(?:\s+[№#]?\s*\d+(?:\s*[,\-\u2013]\s*\d+)*)?",
    re.IGNORECASE,
)
# Bare "кадр 3", "кадры 5,6,7", "кадрах 2-4" with or without number
_RU_BARE_RE = re.compile(
    r"\s*\bкадр\w*(?:\s+[№#]?\s*\d+(?:\s*[,\-\u2013]\s*\d+)*)?",
    re.IGNORECASE,
)
# Bare "frame 4", "frames 5-7"
_EN_BARE_RE = re.compile(
    r"\s*\bframes?(?:\s+[№#]?\s*\d+(?:\s*[,\-\u2013]\s*\d+)*)?",
    re.IGNORECASE,
)


def strip_frame_refs(text: str) -> str:
    """Remove every mention of frame numbers (RU/EN, any grammatical form).

    Strips:
      - "(кадр 5)", "(frames 2-4)"        — parenthesised
      - "на кадрах 5-7", "в кадре 3"      — Russian prepositional
      - "on frame 3", "in frames 5-7"     — English prepositional
      - "кадр 5", "кадры 5,6", "frame 4"  — bare
      - "кадр", "frames"                  — word-only (with no number)
    Then normalises orphaned whitespace/punctuation.
    """
    s = str(text)
    s = _PAREN_FRAME_RE.sub("", s)
    s = _RU_PREP_RE.sub("", s)
    s = _EN_PREP_RE.sub("", s)
    s = _RU_BARE_RE.sub("", s)
    s = _EN_BARE_RE.sub("", s)
    # Normalise leftover whitespace / orphan punctuation
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r":\s*,", ":", s)
    return s.strip(" .,;:-—–")


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
