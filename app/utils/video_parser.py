"""
Parse GPT video-analysis text into a structured dict
compatible with pdf_detailed_service.build_html_detailed().
"""

import re
from datetime import datetime

from app.utils.text_utils import strip_frame_refs


# ── SECTION HEADER DETECTION ───────────────────────────────────────────────────

# Matches ═══ SECTION TITLE ═══  (and variants with spaces / other borders)
_HDR_RE = re.compile(r"[═=\-]{2,}\s*(.+?)\s*[═=\-]{2,}")

_SEC_FRAMES   = re.compile(
    r"frame.by.frame|frame[\s-]+analysis|анализ кадров|кадр.за.кадр|"
    r"покадров|разбор.кадр|speed analysis|кадровый.разбор",
    re.I,
)
_SEC_STR      = re.compile(r"top.?3.?strength|strength|сильн|преимуществ", re.I)
_SEC_WEAK     = re.compile(
    r"top.?3.?area|areas? for (improvement|growth)|time loss|"
    r"weakness|зоны роста|потер|top.?3.?зон|улучш|области.для|time.loss",
    re.I,
)
_SEC_DRILLS   = re.compile(
    r"training drill|drill|recommendation|training plan|"
    r"упражнени|рекоменд|план тренировк",
    re.I,
)
_SEC_SUMMARY  = re.compile(r"\bsummary\b|\boverall score\b|\bитог\b|\bвывод\b", re.I)
_SEC_KEYFRAMES = re.compile(r"key frames|ключевые кадры", re.I)
_SEC_POTENTIAL = re.compile(r"\bpotential\b|\bпотенциал\b", re.I)
_SEC_TECH     = re.compile(
    r"technical breakdown|race breakdown|технич.*?разбор|гоночн.*?разбор",
    re.I,
)
_SEC_RADAR    = re.compile(r"technical profile|технический профиль", re.I)

# ── RADAR MAPPING ─────────────────────────────────────────────────────────────

_RADAR_MAP = {
    'стойка': 'stance',
    'кантование': 'edge',
    'корпус': 'body',
    'руки': 'arms',
    'линия': 'line',
    'баланс': 'balance',
    'stance': 'stance',
    'edging': 'edge',
    'body': 'body',
    'arms': 'arms',
    'line': 'line',
    'balance': 'balance',
}

_RADAR_LINE_RE = re.compile(
    r"^\s*(" + "|".join(_RADAR_MAP.keys()) + r")\s*:\s*(\d+(?:[.,]\d+)?)",
    re.I,
)

# ── FRAME HEADER ───────────────────────────────────────────────────────────────
# Handles: "Frame 1:", "Кадр 5:", "**Кадр 5:**", "#### **Frame 3**", "## Кадр 3"
# Used with .search() — prefix before "frame/кадр" must be only formatting chars.
_FRAME_HDR = re.compile(r"(?:frames?|кадры?)\s*\*?\s*#?\s*(\d+)", re.I)

# ── RATING ─────────────────────────────────────────────────────────────────────

_RATING_FULL = re.compile(
    r"(?:rating|оценка|score)[:\s\-–*]*(\d+(?:[.,]\d+)?)\s*(?:/\s*10)?", re.I
)
_RATING_BARE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*/\s*10\b")

# ── PHASE KEYWORDS ─────────────────────────────────────────────────────────────

_PHASE_RE = re.compile(
    r"(?:phase|фаза|turn phase)[:\s\-–*]*"
    r"(entry|initiation|apex|exit|completion|transition|crossover|"
    r"вход|инициаци|апекс|выход|заверш|переход)",
    re.I,
)
_PHASE_INLINE = re.compile(
    r"\b(entry|initiation|approach|apex|exit|completion|transition|crossover|"
    r"вход|инициаци|апекс|выход|заверш|переход)\b",
    re.I,
)

_PHASE_MAP = {
    "entry": "Entry", "initiation": "Entry", "approach": "Entry",
    "apex": "Apex",
    "exit": "Exit", "completion": "Exit",
    "transition": "Transition", "crossover": "Transition",
    "вход": "Entry", "инициаци": "Entry",
    "апекс": "Apex",
    "выход": "Exit", "заверш": "Exit",
    "переход": "Transition",
}

# ── OBSERVATION LINE ────────────────────────────────────────────────────────────

_OBS_RE = re.compile(
    r"(?:key\s+)?(?:observation|наблюдение|speed(?:\s+observation)?)",
    re.I,
)

# ── OVERALL SCORE ───────────────────────────────────────────────────────────────
# Matches: "оценка техники: 8/10", "**8/10**", "Гоночная оценка техники:** **8/10**"
_SCORE_CONTEXT = re.compile(
    r"(?:overall|technique|race|score|оценка|итог|техник|гоночн)[^\d\n]{0,40}"
    r"(\d+(?:[.,]\d+)?)\s*/\s*10",
    re.I,
)
# Bare "8/10" anywhere — used as fallback
_SCORE_BARE_FALLBACK = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*/\s*10\b")

# ── PHASE SCORES ───────────────────────────────────────────────────────────────
# Matches "entry: 7.5", "вход.*?8/10", "apex — 7/10", etc. on the same line.
# [^/\n]{0,50} stops at "/" so it won't cross into a different X/10 number.
_PHASE_SCORE_RE = {
    "entry":      re.compile(r"(?:entry|вход)[^/\n]{0,50}(\d+(?:[.,]\d+)?)\s*/\s*10", re.I),
    "apex":       re.compile(r"(?:apex|апекс)[^/\n]{0,50}(\d+(?:[.,]\d+)?)\s*/\s*10", re.I),
    "exit":       re.compile(r"(?:exit|выход)[^/\n]{0,50}(\d+(?:[.,]\d+)?)\s*/\s*10", re.I),
    "transition": re.compile(r"(?:transition|переход)[^/\n]{0,50}(\d+(?:[.,]\d+)?)\s*/\s*10", re.I),
}

# ── DRILL PATTERNS ─────────────────────────────────────────────────────────────

_DRILL_RE = re.compile(
    r"(?:\*{0,2}(?:named drill|drill|exercise|упражнение)\*{0,2})[:\s«»\"'*]*"
    r"([^\n\"'»,;]{4,70})",
    re.I,
)
# Parenthetical: (e.g. "javelin turns", ...)
_DRILL_PAREN = re.compile(r'\(e\.?g\.?[^)]*["\u00ab]([^"»\n]{3,50})["\u00bb]', re.I)

# ── WHY / ОПИСАНИЕ ─────────────────────────────────────────────────────────────

_WHY_RE = re.compile(
    r"(?:why it costs|why it matters|mechanism|почему|замедля)[^\n]{0,10}:\s*(.+)",
    re.I,
)


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _to_float(s: str) -> float | None:
    try:
        return float(str(s).replace(",", "."))
    except Exception:
        return None


def _canonical_phase(raw: str) -> str:
    key = raw.lower().strip()
    # First: exact prefix match
    for prefix, canon in _PHASE_MAP.items():
        if key.startswith(prefix):
            return canon
    # Fallback: keyword anywhere in string (handles "Поздний апекс, начало выхода")
    # Priority order: exit > transition > apex > entry (more specific first)
    for priority_prefix in ("выход", "заверш", "exit", "completion",
                             "переход", "crossover", "transition",
                             "апекс", "apex",
                             "вход", "инициаци", "entry", "initiation", "approach"):
        if priority_prefix in key:
            return _PHASE_MAP[priority_prefix]
    return "Entry"


def _strip_md(text: str) -> str:
    """Strip markdown formatting characters from text."""
    return re.sub(r"[*_`\[\]]+", " ", text).strip()


def _clean_str(text: str) -> str:
    """Strip frame refs, markdown bold, and replace long dashes."""
    text = strip_frame_refs(text)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = text.replace(' — ', ' - ').replace('—', '-')
    return text.strip()


def _is_header(line: str) -> bool:
    """True if line looks like a section header."""
    # Markdown ## headers: ## TITLE, ### 1. TITLE, etc.
    if re.match(r"^#{1,6}\s+\S", line):
        return True
    # ═══ TITLE ═══ style
    if _HDR_RE.search(line):
        return True
    # Strip markdown chars for plain-text checks
    plain = _strip_md(re.sub(r"^#{1,6}\s*", "", line))
    stripped = plain.rstrip(": \t")
    # ALL-CAPS word(s) ≥4 chars starting with a letter
    if stripped and stripped == stripped.upper() and len(stripped) >= 4 and stripped[:1].isalpha():
        return True
    # Line ending with colon, short, not starting with a digit
    if plain.rstrip().endswith(":") and len(plain) < 60 and plain[:1] and not plain[0].isdigit():
        return True
    return False


def _detect_section(line: str) -> str | None:
    """Return section name if line is a section header, else None."""
    if not _is_header(line):
        return None

    m = _HDR_RE.search(line)
    if m:
        content = m.group(1)
    else:
        # Strip ## prefix and markdown formatting
        content = re.sub(r"^#{1,6}\s*", "", line)
        content = _strip_md(content)
        # Strip leading "1. ", "2) " numbering
        content = re.sub(r"^\d+[.)]\s*", "", content).strip()

    if _SEC_FRAMES.search(content):
        return "frames"
    if _SEC_STR.search(content):
        return "strengths"
    if _SEC_WEAK.search(content):
        return "weaknesses"
    if _SEC_DRILLS.search(content):
        return "drills"
    if _SEC_KEYFRAMES.search(content):
        return "keyframes"
    if _SEC_POTENTIAL.search(content):
        return "potential"
    if _SEC_SUMMARY.search(content):
        return "summary"
    if _SEC_TECH.search(content):
        return "tech"
    if _SEC_RADAR.search(content):
        return "radar"
    return None


# ── MAIN PARSER ────────────────────────────────────────────────────────────────

def parse_video_analysis(
    text: str,
    athlete: str = "-",
    birth_year: str = "-",
    category: str = "-",
    discipline: str = "-",
    run_type: str = "-",
    frame_paths: list[str] | None = None,
    lang: str = "ru",
) -> dict:
    """
    Parse GPT video-analysis text into a structured dict for pdf_detailed_service.

    Returns dict with keys:
      athlete, birth_year, category, discipline, run_type, score,
      phase_scores, strengths, weaknesses, phases, drills, potential, date
    """
    if frame_paths is None:
        frame_paths = []

    # Strip GPT AI-filler lines from entire input (boilerplate like "let me know if…")
    _boilerplate_re = re.compile(
        r'готов детализировать|напишите|если нужно|let me know|feel free', re.I
    )
    text = '\n'.join(ln for ln in text.splitlines() if not _boilerplate_re.search(ln))

    lines = text.splitlines()

    # ── Parse state
    section: str | None = None
    frame_data: dict[int, dict] = {}   # frame_idx → {phase, rating, obs, issue}
    current_frame: int | None = None

    strengths: list[str] = []
    raw_weak_items: list[dict] = []    # {text, why, drill}
    current_weak: dict | None = None
    _ISPR_PAREN_RE = re.compile(
        r'(?:исправление|correction|fix|упражнение|drill)[^«»"\'(]*'
        r'[«»"\']((?:(?![«»"\']).)*)(?:[«»"\'])\s*\(([^)]{10,300})\)',
        re.I,
    )
    parsed_drills: list[dict] = []     # drills from dedicated drills section
    parsed_key_frames: list[dict] = []  # key frames (strength/weakness/pattern)
    radar: dict[str, int] = {}          # technical profile scores
    current_drill: dict | None = None
    summary_lines: list[str] = []
    potential_lines: list[str] = []
    overall_score: float | None = None
    sections_found: list[str] = []

    def _flush_weak():
        nonlocal current_weak
        if current_weak is not None:
            raw_weak_items.append(current_weak)
            current_weak = None

    def _flush_drill():
        nonlocal current_drill
        if current_drill is not None:
            parsed_drills.append(current_drill)
            current_drill = None

    def _process_frame_content(fd: dict, line: str):
        """Extract rating / phase / observation from a line and update fd in-place."""
        if fd["rating"] is None:
            m = _RATING_FULL.search(line)
            if not m:
                m = _RATING_BARE.search(line)
            if m:
                v = _to_float(m.group(1))
                if v and 1 <= v <= 10:
                    fd["rating"] = v

        if fd["phase"] is None:
            m = _PHASE_RE.search(line)
            if m:
                fd["phase"] = _canonical_phase(m.group(1))
            else:
                m2 = _PHASE_INLINE.search(line)
                if m2:
                    fd["phase"] = _canonical_phase(m2.group(1))

        if _OBS_RE.search(line):
            obs = re.sub(
                r"(?:key\s+)?(?:observation|наблюдение|speed(?:\s+observation)?)[:\s\-–*]*",
                "", line, flags=re.I,
            ).strip().lstrip("•–-*").strip()
            if obs and not fd["obs"]:
                fd["obs"] = obs
        # Capture numbered points (1. text, **1.** text) for obs enrichment
        m_pt = re.match(r'^\*{0,2}(\d)\.\*{0,2}\s+(.{30,})', line)
        if m_pt and int(m_pt.group(1)) <= 5:
            pt_text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', m_pt.group(2)).strip()
            if "points" in fd and len(fd["points"]) < 5:
                fd["points"].append(pt_text)

        elif not fd["obs"] and fd["rating"] is not None and fd["phase"] is not None:
            # Skip lines that are rating/score annotations — not observations
            if re.match(r'[-•*\s]*\*{0,2}(оценка|rating|score|оцен)\b', line, re.I):
                pass
            else:
                clean = re.sub(
                    r"(frame|кадр)\s*\d+|rating\s*[\d./]+|оценка\s*[\d./]+|"
                    r"phase\s*:\s*\w+|фаза\s*:\s*\w+|\d+\s*/\s*10",
                    "", line, flags=re.I,
                )
                clean = re.sub(r"[\-–:*]+", " ", clean).strip()
                if len(clean) > 10:
                    fd["obs"] = clean

    # ── State machine over lines
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # ── Overall score anywhere in text (always try, prefer later occurrences)
        m_score = _SCORE_CONTEXT.search(line)
        if m_score:
            v = _to_float(m_score.group(1))
            if v and 1 <= v <= 10:
                overall_score = v  # keep updating — last match wins for summary context

        # ── Section header detection
        new_sec = _detect_section(line)
        if new_sec:
            if new_sec not in sections_found:
                sections_found.append(new_sec)
            prev_section = section
            section = new_sec
            current_frame = None
            _flush_weak()
            _flush_drill()
            print(f"[parser] section: {prev_section!r} → {new_sec!r}  line={line[:70]!r}", flush=True)
            continue

        # ── Auto-detect frames section: if a frame header appears before any section is set
        if section is None and _FRAME_HDR.search(line):
            prefix_raw = line[: _FRAME_HDR.search(line).start()]
            prefix_clean = re.sub(r"[\d#*\s\-•·_()\[\].:]+", "", prefix_raw)
            if len(prefix_clean) == 0:
                section = "frames"
                if "frames" not in sections_found:
                    sections_found.append("frames")
                print(f"[parser] auto-detected section: None → 'frames'  line={line[:70]!r}", flush=True)

        # ═════════════════════════════════════ FRAMES
        if section == "frames":
            # ── Markdown table row: | N | phase | rating | observation |
            m_tbl = re.match(r"^\|\s*(\d+)\s*\|([^|]+)\|([^|]+)\|([^|]*)", line)
            if m_tbl and not re.match(r"^\|\s*[-:]+\s*\|", line):
                try:
                    fnum = int(m_tbl.group(1).strip())
                    phase_s = m_tbl.group(2).strip()
                    rating_s = m_tbl.group(3).strip()
                    obs_s = _strip_md(m_tbl.group(4).strip())
                    m_rng = re.match(
                        r"(\d+(?:[.,]\d+)?)\s*[–\-]\s*(\d+(?:[.,]\d+)?)", rating_s
                    )
                    if m_rng:
                        rv = (_to_float(m_rng.group(1)) + _to_float(m_rng.group(2))) / 2
                    else:
                        rv = _to_float(rating_s)
                    if fnum > 0 and rv and 1 <= rv <= 10:
                        if fnum not in frame_data:
                            frame_data[fnum] = {
                                "phase": _canonical_phase(phase_s),
                                "rating": rv,
                                "obs": obs_s,
                                "issue": "", "points": [],
                            }
                            print(
                                f"[parser] frame {fnum} table  phase={_canonical_phase(phase_s)} rating={rv}",
                                flush=True,
                            )
                except (ValueError, TypeError):
                    pass
                continue

            m_hdr = _FRAME_HDR.search(line)
            if m_hdr:
                # Only a frame header if everything before "frame/кадр" is
                # purely formatting characters (##, **, spaces, dashes, bullets)
                prefix_raw = line[: m_hdr.start()]
                # Allow formatting chars AND numbered-list prefixes (e.g. "1. ", "2) ")
                prefix_clean = re.sub(r"[\d#*\s\-•·_()\[\].:]+", "", prefix_raw)
                if len(prefix_clean) == 0:
                    current_frame = int(m_hdr.group(1))
                    if current_frame not in frame_data:
                        frame_data[current_frame] = {
                            "phase": None, "rating": None,
                            "obs": "", "issue": "", "points": [],
                        }
                    # Process remainder of same line (inline format support)
                    tail = line[m_hdr.end():].strip().lstrip(":*–— \t").strip()
                    tail = re.sub(r"^[–—\-]+\s*", "", tail).strip()
                    if tail:
                        _process_frame_content(frame_data[current_frame], tail)
                    print(f"[parser] frame {current_frame} detected  line={line[:60]!r}", flush=True)
                    continue

            if current_frame is not None:
                _process_frame_content(frame_data[current_frame], line)

        # ═════════════════════════════════════ STRENGTHS
        elif section == "strengths":
            # Bold item: **text**
            m_bold = re.match(r"^\*{1,2}([^*]{4,})\*{1,2}\s*$", line)
            if m_bold:
                item = m_bold.group(1).strip().rstrip(":").strip()
                if item and len(strengths) < 3:
                    strengths.append(item)
            elif re.match(r"^[•\-·]", line):
                item = re.sub(r"^[•\-·]+\s*", "", line).strip()
                item = _strip_md(item)
                if item and len(strengths) < 3:
                    strengths.append(item)
            elif re.match(r"^\d+[\.\)]\s+\S", line):
                item = re.sub(r"^\d+[\.\)]\s+", "", line).strip()
                item = _strip_md(item)
                if item and len(strengths) < 3:
                    strengths.append(item)

        # ═════════════════════════════════════ WEAKNESSES
        elif section == "weaknesses":
            is_bullet   = bool(re.match(r"^[•\-·]", line))
            is_numbered = bool(re.match(r"^\d+[\.\)]\s+\S", line))

            # Bold heading on its own line: **Frame 13 (Apex skidding):** or **1. Upper body rotation**
            m_bold_hdr = re.match(r"^\*{1,2}(\d+[\.\)]\s+)?([^*\n]{4,}?)\*{0,2}:?\s*$", line)

            if is_bullet or is_numbered:
                item = re.sub(r"^[•\-·]+\s*", "", line)
                item = re.sub(r"^\d+[\.\)]\s+", "", item).strip()
                item = _strip_md(item)
                is_sub = bool(re.search(
                    r"^(why|drill|exercise|named|corrective|technical fault|"
                    r"where|how to|in gs|in sl|"
                    r"упражнение|почему|механизм|исправление|где|когда|как|"
                    r"что|для|в gs|в sl|потеря|потому|это|скорость|"
                    r"mechanism|because|result)",
                    item, re.I,
                ))
                if not is_sub and item:
                    _flush_weak()
                    current_weak = {"text": item, "why": "", "drill": "", "drill_desc": ""}

            elif not is_bullet and not is_numbered:
                # Standalone bold heading **text** OR markdown heading ### N. text
                m_md_hdr = re.match(r"^#{1,4}\s+(\d+[\.\)]\s+)?(.{4,})", line)
                heading_text = None
                if m_bold_hdr:
                    heading_text = _strip_md(m_bold_hdr.group(2) or "").strip().rstrip(":")
                    heading_text = re.sub(r"^\d+[\.\)]\s*", "", heading_text).strip()
                elif m_md_hdr:
                    heading_text = _strip_md(m_md_hdr.group(2) or "").strip().rstrip(":")
                if heading_text and len(heading_text) >= 4:
                    _flush_weak()
                    current_weak = {"text": heading_text, "why": "", "drill": ""}

            elif current_weak is not None and not _is_header(line):
                # Plain continuation line — append real description to the weakness
                cont = _strip_md(line).strip().lstrip("–—•").strip()
                if cont and len(current_weak["text"]) < 180:
                    # If weakness text is just a bare "Frame N" title, replace it
                    if re.match(r"^(?:frame|кадр)\s*\d+[^a-zA-Zа-яА-Я]*$",
                                current_weak["text"], re.I):
                        current_weak["text"] = cont
                    else:
                        current_weak["text"] += " " + cont

            if current_weak is not None:
                # Try to extract drill name + description from "Исправление: упражнение «NAME» (DESC)"
                m_ispr = _ISPR_PAREN_RE.search(line)
                if m_ispr:
                    if not current_weak["drill"]:
                        current_weak["drill"] = m_ispr.group(1).strip().strip('"«»* _').strip()
                    if not current_weak["drill_desc"]:
                        current_weak["drill_desc"] = m_ispr.group(2).strip()
                else:
                    m = _DRILL_RE.search(line)
                    if m and not current_weak["drill"]:
                        current_weak["drill"] = _strip_md(m.group(1)).strip().strip('"«»* _').strip()
                    if not current_weak["drill"]:
                        m = _DRILL_PAREN.search(line)
                        if m:
                            current_weak["drill"] = _strip_md(m.group(1)).strip().strip('"«»* _').strip()
                    # Quoted drill name at line start: "Name" — description  or  «Name» — ...
                    if not current_weak["drill"]:
                        m = re.match(
                            r'^[-•*\s]*\*{0,2}["\«\u201c\u201e]([^"\»\u201d\n]{3,60})["\»\u201d]\*{0,2}',
                            line,
                        )
                        if m:
                            current_weak["drill"] = m.group(1).strip()
                m = _WHY_RE.search(line)
                if m and not current_weak["why"]:
                    current_weak["why"] = m.group(1).strip()

        # ═════════════════════════════════════ DRILLS (dedicated section)
        elif section == "drills":
            # New structured format: "1. Name — action"
            m_num = re.match(r"^\d+[\.\)]\s+(.+)", line)
            # Structured fields: ▸ Что делать: / ▸ Action: / ▸ Фокус: / ▸ Focus: / ▸ Успех: / ▸ Success:
            m_field = re.match(
                r"^▸\s*(Что делать|Action|Фокус|Focus|Успех|Success)\s*:\s*(.+)",
                line, re.I,
            )
            if m_field and current_drill is not None:
                field_name = m_field.group(1).strip().lower()
                field_val = _strip_md(m_field.group(2)).strip()
                if field_name in ("что делать", "action"):
                    current_drill["action"] = field_val
                elif field_name in ("фокус", "focus"):
                    current_drill["focus"] = field_val
                elif field_name in ("успех", "success"):
                    current_drill["success"] = field_val
            elif m_num:
                _flush_drill()
                name = _strip_md(m_num.group(1)).strip().strip('"«»* _').strip()
                current_drill = {
                    "name": name,
                    "desc": "",
                    "action": "",
                    "focus": "",
                    "success": "",
                }
            else:
                # Old format fallback: named drill pattern
                m = _DRILL_RE.search(line)
                if m:
                    _flush_drill()
                    current_drill = {
                        "name": _strip_md(m.group(1)).strip().strip('"«»* _').strip(),
                        "desc": "",
                        "action": "",
                        "focus": "",
                        "success": "",
                    }
                elif current_drill is not None:
                    if not re.match(r"^\d+[\.\)]", line):
                        sep = " " if current_drill["desc"] else ""
                        current_drill["desc"] += sep + _strip_md(line)

        # ═════════════════════════════════════ KEY FRAMES
        elif section == "keyframes":
            # Parse variants:
            #   "СИЛА: кадр 5" / "STRENGTH: frame 5"
            #   "1. **СИЛА — кадр 3**" / "1. **STRENGTH:** frame 13"
            m_type = re.match(
                r"^(?:\d+[\.\)]\s*)?[\*]*"
                r"(СИЛА|ПРОБЛЕМА|ПАТТЕРН|STRENGTH|WEAKNESS|PATTERN)"
                r"[\*]*\s*[\:\—\-–]+\s*[\*]*\s*(?:кадр|frame)\s+(\d+)",
                line, re.I,
            )
            m_caption = re.match(r"^▸\s*(?:Подпись|Caption)\s*:\s*(.+)", line, re.I)
            m_conf = re.match(r"^▸\s*(?:Уверенность|Confidence)\s*:\s*(.+)", line, re.I)
            if m_type:
                _type_map = {
                    "сила": "strength", "strength": "strength",
                    "проблема": "weakness", "weakness": "weakness",
                    "паттерн": "pattern", "pattern": "pattern",
                }
                parsed_key_frames.append({
                    "type": _type_map.get(m_type.group(1).lower(), "pattern"),
                    "frame_index": int(m_type.group(2)),
                    "caption": "",
                    "confidence": "",
                })
            elif m_caption and parsed_key_frames:
                parsed_key_frames[-1]["caption"] = m_caption.group(1).strip()
            elif m_conf and parsed_key_frames:
                parsed_key_frames[-1]["confidence"] = m_conf.group(1).strip()

        # ═════════════════════════════════════ SUMMARY
        elif section == "summary":
            summary_lines.append(line)

        # ═════════════════════════════════════ RADAR (TECHNICAL PROFILE)
        elif section == "radar":
            m_radar = _RADAR_LINE_RE.match(line)
            if m_radar:
                key = _RADAR_MAP.get(m_radar.group(1).lower())
                val = _to_float(m_radar.group(2))
                if key and val is not None and 1 <= val <= 10:
                    radar[key] = int(val)

        # ═════════════════════════════════════ POTENTIAL
        elif section == "potential":
            potential_lines.append(line)

    # Flush pending items
    _flush_weak()
    _flush_drill()

    # ── Overall score: fallback scans
    if overall_score is None:
        # Scan last 30% of lines for any X/10
        cutoff = int(len(lines) * 0.7)
        for ln in lines[cutoff:]:
            m = _SCORE_BARE_FALLBACK.search(ln)
            if m:
                v = _to_float(m.group(1))
                if v and 1 <= v <= 10:
                    overall_score = v
                    print(f"[parser] overall_score from last-30%: {v}  line={ln[:60]!r}", flush=True)
                    break
    if overall_score is None:
        for m in _SCORE_BARE_FALLBACK.finditer(text):
            v = _to_float(m.group(1))
            if v and 1 <= v <= 10:
                overall_score = v
                break
    if overall_score is None:
        overall_score = 7.5

    # ── Phase scores: explicit line-level patterns → average of frames → default 7.5
    phase_scores: dict[str, float] = {}
    for key, pat in _PHASE_SCORE_RE.items():
        for ln in lines:
            m = pat.search(ln)
            if m:
                v = _to_float(m.group(1))
                if v and 1 <= v <= 10:
                    phase_scores[key] = v
                    break

    # Accumulate frame ratings by phase
    phase_frame_ratings: dict[str, list[float]] = {
        "entry": [], "apex": [], "exit": [], "transition": []
    }
    for fd in frame_data.values():
        ph = (fd.get("phase") or "").lower()
        r = fd.get("rating")
        if ph in phase_frame_ratings and r:
            phase_frame_ratings[ph].append(r)

    for key in ("entry", "apex", "exit", "transition"):
        if key not in phase_scores:
            vals = phase_frame_ratings[key]
            phase_scores[key] = round(sum(vals) / len(vals), 1) if vals else 7.5

    # ── Best frame per phase (highest rating)
    best_by_phase: dict[str, dict] = {}
    for idx, fd in frame_data.items():
        ph = fd.get("phase")
        if not ph:
            continue
        r = fd.get("rating") or 0.0
        prev = best_by_phase.get(ph)
        if prev is None or r > prev.get("rating", 0):
            best_by_phase[ph] = {"frame_idx": idx, **fd}

    # ── Build phases list (fixed order)
    phase_display: dict[str, str] = {
        "Entry":      "Entry"      if lang == "en" else "Вход",
        "Apex":       "Apex"       if lang == "en" else "Апекс",
        "Exit":       "Exit"       if lang == "en" else "Выход",
        "Transition": "Transition" if lang == "en" else "Переход",
    }

    phases: list[dict] = []
    for ph_canon in ("Entry", "Apex", "Exit", "Transition"):
        fd = best_by_phase.get(ph_canon)
        if fd is not None:
            fidx = fd["frame_idx"]
            fp = (
                frame_paths[fidx - 1]
                if 0 <= fidx - 1 < len(frame_paths)
                else None
            )
            frame_time = f"{fidx / 2.0:.1f}s"
            obs = fd.get("obs") or ""
            # Strip markdown bold formatting from obs
            obs = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', obs).strip()
            # Strip "Скорость: " / "Speed: " prefix left by GPT observation labels
            obs = re.sub(r'^(Скорость|Speed|скорост\w*)[:\s]+', '', obs, flags=re.IGNORECASE).strip()
            # Enrich short obs with first numbered point from frame analysis
            if len(obs) < 100 and fd.get("points"):
                extra = fd["points"][0]
                if extra and extra not in obs:
                    obs = obs + (" " if obs else "") + extra
        else:
            continue  # skip phases with no frames detected

        phases.append({
            "name":        phase_display[ph_canon],
            "phase":       ph_canon,
            "score":       phase_scores.get(ph_canon.lower(), 7.5),
            "frame_path":  fp,
            "frame_time":  frame_time,
            "observation": obs,
            "issue":       "",
        })

    # ── Weaknesses list (plain text, up to 3)
    # Filter out bare "Frame N" titles that have no real description attached
    _useful_weak = [
        w for w in raw_weak_items
        if not re.match(r"^(?:frame|кадр)\s*\d+\s*[:\(]?$", w["text"], re.I)
    ]
    weaknesses: list[str] = [w["text"] for w in _useful_weak[:3]]

    # ── Drills: prefer dedicated drills section, fall back to weakness-embedded drills
    drills: list[dict] = []
    if parsed_drills:
        for i, pd in enumerate(parsed_drills[:4], 1):
            # Build description from structured fields if available
            action_txt = pd.get("action", "").strip()
            focus_txt = pd.get("focus", "").strip()
            success_txt = pd.get("success", "").strip()
            if action_txt or focus_txt or success_txt:
                desc = pd["desc"] or pd["name"]
            else:
                desc = pd["desc"] or pd["name"]
            drills.append({
                "number":      i,
                "name":        pd["name"],
                "description": desc,
                "action":      action_txt,
                "focus":       focus_txt,
                "success":     success_txt,
                "priority":    (i == 1),
            })
    else:
        # Also filter out context/location items (e.g. "Где: середина трассы")
        _drill_source = [
            w for w in (_useful_weak if _useful_weak else raw_weak_items)
            if not re.match(
                r"^(?:где|where|когда|when|почему|why|как|how|потому|because"
                r"|механизм|mechanism|результат|result)\s*[:\-–]",
                w["text"], re.I,
            )
        ]
        for i, wi in enumerate(_drill_source[:4], 1):
            name = wi.get("drill") or ""
            if not name:
                # Try to extract a quoted drill name from the text
                # e.g.  "Блокировка плеча" — description...
                #       «Апекс вверх» — description...
                m_q = re.search(r'["\«\u201c\u201e]([^"\»\u201d\n]{3,60})["\»\u201d]', wi["text"])
                if m_q:
                    name = m_q.group(1).strip()
                else:
                    name = wi["text"][:60]
            # Strip "corrective drill:" / "named drill:" prefix
            name = re.sub(r"^(?:corrective\s+|named\s+)?drill\s*:\s*", "", name, flags=re.I).strip()
            # Trim trailing " — description" or ": description" after the drill name
            name = re.sub(r"\s*[–—\-:]+\s*.{10,}$", "", name).strip()
            # Trim incomplete trailing parenthetical: "(drill", "(упр", "(e.g"
            name = re.sub(r"\s*\([^)]{0,25}$", "", name).strip()
            name = name[:70]
            # Build description: prefer parenthetical drill desc, then why, then weakness text
            drill_desc_txt = wi.get("drill_desc", "").strip()
            why_txt        = wi.get("why",        "").strip()
            body_txt       = wi.get("text",       "").strip()
            if drill_desc_txt and len(drill_desc_txt) >= 20:
                desc = drill_desc_txt
            elif why_txt and len(why_txt) >= 50:
                desc = why_txt
            elif why_txt and body_txt and body_txt != name:
                desc = f"{body_txt}. {why_txt}"
            elif body_txt and body_txt != name:
                desc = body_txt
            elif name:
                desc = f"Упражнение «{name}» — выполнять на тренировке для устранения ошибки."
            else:
                desc = "—"
            # Ensure minimum description length
            if desc and desc != "—" and len(desc) < 50 and name:
                desc = f"{desc}. Выполнять регулярно для устранения ошибки в технике."
            # Strip markdown bold from drill name and description
            name = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', name).strip()
            desc = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', desc).strip()
            drills.append({
                "number":      i,
                "name":        name,
                "description": desc,
                "action":      "",
                "focus":       "",
                "success":     "",
                "priority":    (i == 1),
            })
    if not drills:
        drills = [{
            "number": 1,
            "name": "Review technique" if lang == "en" else "Разбор техники",
            "description": "-",
            "action": "",
            "focus": "",
            "success": "",
            "priority": True,
        }]

    # ── Potential text (preserve bullet structure with newlines)
    potential = "\n".join(potential_lines).strip()
    if not potential:
        summary_tail = [ln for ln in summary_lines if not _RATING_BARE.search(ln)]
        potential = " ".join(summary_tail[-2:]).strip()
    if not potential:
        potential = "-"
    # Strip everything after "---" separator (AI footer) and boilerplate phrases
    potential = re.split(r'\s*---', potential)[0].strip()
    potential = re.sub(
        r'[\.\s]*(готов детализировать|напишите|если нужно|let me know|feel free)[^.]*\.?',
        '', potential, flags=re.I,
    ).strip()
    # Strip markdown bold from potential
    potential = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', potential).strip()

    # ── Debug summary
    print(
        f"[parser] SUMMARY sections={sections_found} frames={len(frame_data)} "
        f"strengths={len(strengths)} weaknesses={len(raw_weak_items)} "
        f"drills_dedicated={len(parsed_drills)} overall={overall_score} "
        f"phase_scores={phase_scores}",
        flush=True,
    )
    print(f"[parser] frame_data keys={list(frame_data.keys())}", flush=True)
    print(f"[parser] drills={[d['name'] for d in drills]}", flush=True)

    # Clean all text fields
    strengths  = [_clean_str(s) for s in strengths]
    weaknesses = [_clean_str(s) for s in weaknesses]
    potential  = _clean_str(potential)
    for ph in phases:
        ph["observation"] = _clean_str(ph.get("observation", ""))
        ph["issue"]       = _clean_str(ph.get("issue", ""))
    for dr in drills:
        dr["name"]        = _clean_str(dr.get("name", ""))
        dr["description"] = _clean_str(dr.get("description", ""))

    # ── Resolve key frame paths
    key_frames: list[dict] = []
    for kf in parsed_key_frames:
        fidx = kf["frame_index"]
        fp = frame_paths[fidx - 1] if 0 <= fidx - 1 < len(frame_paths) else None
        key_frames.append({**kf, "frame_path": fp})
    print(f"[parser] key_frames={[(kf['type'], kf['frame_index']) for kf in key_frames]}", flush=True)

    return {
        "athlete":      athlete,
        "birth_year":   str(birth_year),
        "category":     category,
        "discipline":   discipline,
        "run_type":     run_type,
        "score":        f"{overall_score:.1f}",
        "phase_scores": phase_scores,
        "strengths":    strengths,
        "weaknesses":   weaknesses,
        "phases":       phases,
        "drills":       drills,
        "key_frames":   key_frames,
        "best_frames":  [],  # deprecated — KEY FRAMES removed from prompts
        "radar":        radar,
        "potential":    potential,
        "date":         datetime.now().strftime("%d.%m.%Y"),
    }
