import re

from app.utils.text_utils import _clamp, strip_frame_refs

# === TELEGRAM TEXT LIMITS (chars) ===
_TG = {
    "strength": 120,
    "weakness": 120,
    "drill": 80,
    "potential": 200,
    "limitations": 200,
}


def _clean(line: str) -> str:
    # убираем эмодзи-префиксы
    replacements = {
        "🏔": "", "👤": "", "🎂": "", "🏷": "", "📊": "",
        "⚖️": "", "✅": "", "⚠️": "", "🎯": "", "📈": "",
        "🔹": "", "📷": "",
    }
    for old, new in replacements.items():
        line = line.replace(old, new)

    # убираем markdown bold/italic (gpt-4.1 активно использует)
    line = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
    line = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", line)

    # убираем markdown заголовки (### Рекомендации)
    line = re.sub(r"^#{1,4}\s*", "", line)

    # длинное тире → короткое
    line = line.replace("—", "-")

    line = re.sub(r"\s+", " ", line).strip()
    return line


def _shorten(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1].rstrip() + "…"


def _bullet(text: str) -> str:
    text = text.strip()
    if not text:
        return "• —"
    return f"• {text}"


def _is_separator(line: str) -> bool:
    """Строки-разделители типа --- или === от gpt-4.1."""
    return bool(re.match(r"^[-=]{3,}$", line))


def _is_video_format(text: str) -> bool:
    """Detect if GPT response uses ═══ video section headers."""
    return "═══" in text


# ── VIDEO FORMATTER ──────────────────────────────────────────────────────────

_SEC_RE = re.compile(r"[═=]{2,}\s*(.+?)\s*[═=]{2,}")

def _detect_video_section(line: str) -> str | None:
    """Detect video section from ═══ HEADER ═══ lines."""
    m = _SEC_RE.search(line)
    if not m:
        return None
    content = m.group(1).upper()
    if re.search(r"СИЛЬН|STRENGTH|ПРЕИМУЩЕСТВ", content):
        return "strengths"
    if re.search(r"ПОТЕР|WEAKNESS|ЗОНЫ РОСТА|AREA|GROWTH|TIME LOSS|УЛУЧШ", content):
        return "weaknesses"
    if re.search(r"УПРАЖНЕН|DRILL|РЕКОМЕНД|RECOMMEND", content):
        return "drills"
    if re.search(r"ПОТЕНЦИАЛ|POTENTIAL", content):
        return "potential"
    if re.search(r"ИТОГ|SUMMARY", content):
        return "summary"
    if re.search(r"КЛЮЧЕВ|KEY FRAME", content):
        return "keyframes"
    if re.search(r"ТЕХНИЧЕСКИЙ ПРОФИЛЬ|TECHNICAL PROFILE", content):
        return "skip"  # radar data — not shown in Telegram
    if re.search(r"КАДР|FRAME|АНАЛИЗ|ANALYSIS|ТЕХНИЧЕСК|TECHNICAL|ГОНОЧН|RACE|РАЗБОР|BREAKDOWN", content):
        return "skip"
    return None


def _format_video(raw_text: str, lang: str, extra_data: dict | None = None) -> str:
    """Format video GPT response for Telegram summary."""
    if lang == "en":
        lbl_score = "Overall score"
        lbl_strengths = "Strengths"
        lbl_weaknesses = "Growth areas"
        lbl_drills = "Drills"
        lbl_potential = "Potential"
        fb_strengths = ["• Good base technique"]
        fb_weaknesses = ["• Needs improvement"]
        fb_drills = ["• Work on technique"]
        fb_potential = "Good potential for growth."
    else:
        lbl_score = "Общая оценка"
        lbl_strengths = "Сильные стороны"
        lbl_weaknesses = "Зоны роста"
        lbl_drills = "Упражнения"
        lbl_potential = "Потенциал"
        fb_strengths = ["• Базовая техника сформирована"]
        fb_weaknesses = ["• Требуется доработка"]
        fb_drills = ["• Работа над техникой"]
        fb_potential = "Есть потенциал для роста."

    section = None
    score = ""
    strengths: list[str] = []
    weaknesses: list[str] = []
    drills: list[str] = []
    potential_lines: list[str] = []
    limitations: str = ""

    for raw_line in raw_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        # Detect section change
        sec = _detect_video_section(line)
        if sec is not None:
            section = sec
            continue

        # Skip frame-by-frame, tech breakdown, key frames sections
        if section in ("skip", "keyframes"):
            continue

        # SUMMARY section — extract score
        if section == "summary":
            m_sc = re.search(r"(\d+(?:[.,]\d+)?)\s*/\s*10", line)
            if m_sc and not score:
                score = f"{m_sc.group(1)}/10"
            continue

        # STRENGTHS — numbered items
        if section == "strengths":
            m_num = re.match(r"^\d+[\.\)]\s*(.+)", line)
            if m_num and len(strengths) < 3:
                name = re.sub(r"\*{1,2}", "", m_num.group(1)).strip()
                # Strip internal prompt markers
                name = re.sub(r"\s*\(\s*(?:СТРОГО|STRICTLY)[^)]*\)", "", name, flags=re.I)
                name = re.sub(r"\s*\(\s*(?:НЕ другая группа|NOT any other group)[^)]*\)", "", name, flags=re.I)
                # Strip all frame references (RU/EN, any grammatical form)
                name = strip_frame_refs(name)
                # Take text before first long dash explanation (but keep short dashes in names)
                name = re.split(r"\s+[-–—]\s+", name, maxsplit=1)[0].strip()
                name = _clamp(name, _TG["strength"], "sentence")
                strengths.append(f"• {name}")
            continue

        # WEAKNESSES — numbered items (extract name before explanation)
        if section == "weaknesses":
            m_num = re.match(r"^\d+[\.\)]\s*(.+)", line)
            if m_num and len(weaknesses) < 3:
                name = re.sub(r"\*{1,2}", "", m_num.group(1)).strip()
                # Strip frame refs (RU/EN), then stray percentages
                name = strip_frame_refs(name)
                name = re.sub(r",?\s*[≈~]?\d+[–-]\d+%.*$", "", name)
                name = _clamp(name, _TG["weakness"], "sentence")
                weaknesses.append(f"• {name}")
            continue

        # DRILLS — numbered items (just names)
        if section == "drills":
            m_num = re.match(r"^\d+[\.\)]\s*(.+)", line)
            if m_num and len(drills) < 3:
                name = re.sub(r"\*{1,2}", "", m_num.group(1)).strip()
                name = strip_frame_refs(name)
                name = _clamp(name, _TG["drill"], "word")
                drills.append(f"• {name}")
            continue

        # POTENTIAL — collect text lines (skip numbered prefix if present)
        if section == "potential":
            cl = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
            # Strip internal prompt-instruction markers that GPT may echo
            cl = re.sub(r"\s*\(\s*(?:СТРОГО|STRICTLY)[^)]*\)", "", cl, flags=re.I)
            cl = re.sub(r"\s*\(\s*(?:НЕ другая группа|NOT any other group)[^)]*\)", "", cl, flags=re.I)
            # Convert **bold** markdown to <b>HTML</b> BEFORE stripping leftover stars
            cl = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", cl)
            # Strip any remaining leading/trailing bold markers
            cl = re.sub(r"^\*{1,2}(.+?)\*{0,2}$", r"\1", cl).strip()
            # Skip instruction-like lines
            if any(k in cl.upper() for k in ["ОБЯЗАТЕЛЬНО", "MANDATORY", "КАЖДЫЙ ПУНКТ", "EACH POINT"]):
                continue
            if cl.startswith("- "):
                cl = cl[2:].strip()
            # Separate "Ограничения анализа" / "Analysis limitations" into footnote
            cl_upper = cl.upper()
            if any(k in cl_upper for k in ["ОГРАНИЧЕНИ", "LIMITATION"]):
                # Extract text after the label
                lim_text = re.sub(r"^<b>[^<]+</b>\s*[-:–]\s*", "", cl).strip()
                if not lim_text:
                    lim_text = re.sub(r"^[^:–-]+[-:–]\s*", "", cl).strip()
                limitations = lim_text or cl
                continue
            if cl and len(potential_lines) < 4:
                potential_lines.append(cl)
            continue

    # Build output
    output = []

    if lang == "en":
        output.append("🏔 Ski Technique Analysis")
    else:
        output.append("🏔 Анализ техники")

    if score:
        output.append(f"\n📊 {lbl_score}: {score}")

    output.append(f"\n✅ {lbl_strengths}:")
    output.extend(strengths or fb_strengths)

    output.append(f"\n⚠️ {lbl_weaknesses}:")
    output.extend(weaknesses or fb_weaknesses)

    output.append(f"\n🎯 {lbl_drills}:")
    output.extend(drills or fb_drills)

    if potential_lines:
        output.append(f"\n📈 {lbl_potential}:")
        for pl in potential_lines:
            output.append(f"  - {_clamp(pl, _TG['potential'], 'sentence')}")
    else:
        output.append(f"\n📈 {lbl_potential}:")
        output.append(fb_potential)

    if limitations:
        output.append(f"\n<i>⚠️ {_clamp(limitations, _TG['limitations'], 'sentence')}</i>")

    return "\n".join(output).strip()


# ── PHOTO FORMATTER ──────────────────────────────────────────────────────────

def _format_photo(raw_text: str, lang: str, extra_data: dict | None = None) -> str:
    """Format photo GPT response for Telegram summary (original formatter)."""
    lines = raw_text.split("\n")

    if lang == "en":
        lbl_name      = "Name"
        lbl_born      = "Year of birth"
        lbl_cat       = "Category"
        lbl_score     = "Overall score"
        lbl_strengths = "Strengths"
        lbl_weaknesses= "Areas for growth"
        lbl_recs      = "Recommendations"
        lbl_potential = "Potential"
        fb_strengths  = ["• Good base technique established"]
        fb_weaknesses = ["• Stability needs further work"]
        fb_recs       = ["• Continue working on technique quality"]
        fb_potential  = "Good potential for further growth with consistent training."
    else:
        lbl_name      = "Имя"
        lbl_born      = "Год рождения"
        lbl_cat       = "Категория"
        lbl_score     = "Общая оценка"
        lbl_strengths = "Сильные стороны"
        lbl_weaknesses= "Зоны роста"
        lbl_recs      = "Рекомендации"
        lbl_potential = "Потенциал"
        fb_strengths  = ["• Базовая техника сформирована"]
        fb_weaknesses = ["• Требуется доработка стабильности"]
        fb_recs       = ["• Продолжить работу над качеством техники"]
        fb_potential  = "Есть потенциал для дальнейшего роста при доработке ключевых технических элементов."

    data = {
        "title": "Анализ техники",
        "athlete": "",
        "birth_year": "",
        "category": "",
        "score": "",
        "details": [],
        "strengths": [],
        "weaknesses": [],
        "recommendations": [],
        "potential": [],
    }

    section = None

    for raw in lines:
        line = _clean(raw)
        if not line:
            continue

        if _is_separator(line):
            continue

        # --- HEADER ---
        if any(k in line for k in ("Анализ техники", "Technique Analysis", "Analysis")):
            data["title"] = line

        elif line.startswith("Имя:") or line.startswith("Name:"):
            data["athlete"] = re.sub(r"^(Имя:|Name:)\s*", "", line).strip()

        elif line.startswith("Год рождения:") or line.startswith("Year of birth:") or line.startswith("Birth year:"):
            data["birth_year"] = re.sub(r"^(Год рождения:|Year of birth:|Birth year:)", "", line).strip()

        elif line.startswith("Категория:") or line.startswith("Category:"):
            data["category"] = re.sub(r"^(Категория:|Category:)", "", line).strip()

        elif "Общая оценка:" in line or "Overall score:" in line or "Overall:" in line:
            for kw in ("Общая оценка:", "Overall score:", "Overall:"):
                if kw in line:
                    data["score"] = line.split(kw, 1)[1].strip()
                    break

        # --- SECTIONS ---
        elif any(k in line for k in ("Сильные стороны", "Strengths", "Strong sides")):
            section = "strengths"

        elif any(k in line for k in ("Зоны роста", "Ошибки", "Areas for growth", "Weaknesses", "Areas of growth")):
            section = "weaknesses"

        elif any(k in line for k in ("Рекомендации", "Recommendations", "Упражнения", "Drills")):
            section = "recommendations"

        elif any(k in line for k in ("Потенциал", "Potential")):
            section = "potential"

        elif "не хватает" in line.lower() or "missing" in line.lower() or "what's missing" in line.lower():
            section = "skip"

        # --- DETAILS (оценки X/10 or — for unavailable) ---
        elif ("/10" in line or "— " in line) and (" - " in line or " — " in line or "—" in line):
            if len(data["details"]) < 5:
                data["details"].append(_clamp(line, 60, "word"))

        elif section == "skip":
            continue

        # --- DRILL STRUCTURED FIELDS (▸ Что делать: / ▸ Action: etc.) ---
        elif line.startswith("▸") and section == "recommendations":
            if data["recommendations"]:
                data["recommendations"][-1] += "\n  " + line

        # --- NUMBERED DRILL ITEMS (1. Name — action) ---
        elif re.match(r"^\d+[\.\)]\s+", line) and section == "recommendations":
            item = re.sub(r"^\d+[\.\)]\s+", "", line).strip()
            if item and len(data["recommendations"]) < 3:
                data["recommendations"].append(_bullet(item))

        # --- BULLETS ---
        elif line.startswith(("•", "-", "·")):
            item = re.sub(r"^[•\-·]\s*", "", line).strip()
            if not item:
                continue

            if section == "potential" and ("не хватает" in raw.lower() or "📷" in raw):
                continue

            if section == "strengths" and len(data["strengths"]) < 3:
                data["strengths"].append(_bullet(item))

            elif section == "weaknesses" and len(data["weaknesses"]) < 3:
                data["weaknesses"].append(_bullet(item))

            elif section == "recommendations" and len(data["recommendations"]) < 3:
                data["recommendations"].append(_bullet(item))

        # --- POTENTIAL ---
        else:
            if section == "potential" and len(data["potential"]) < 3:
                if "не хватает" not in line.lower() and "ракурс" not in line.lower():
                    data["potential"].append(line)

    if not data["potential"]:
        data["potential"] = [fb_potential]

    potential_text = " ".join(data["potential"])

    output = []

    output.append(data["title"])

    if data["athlete"]:
        output.append(f"{lbl_name}: {data['athlete']}")
    if data["birth_year"]:
        output.append(f"{lbl_born}: {data['birth_year']}")
    if data["category"]:
        output.append(f"{lbl_cat}: {data['category']}")
    if data["score"]:
        output.append(f"{lbl_score}: {data['score']}")

    for item in data["details"]:
        output.append(item)

    output.append("")
    output.append(f"{lbl_strengths}:")
    output.extend(data["strengths"] or fb_strengths)

    output.append("")
    output.append(f"{lbl_weaknesses}:")
    output.extend(data["weaknesses"] or fb_weaknesses)

    output.append("")
    output.append(f"{lbl_recs}:")
    output.extend(data["recommendations"] or fb_recs)

    output.append("")
    output.append(f"{lbl_potential}:")
    output.append(potential_text)

    return "\n".join(output).strip()


# ── PUBLIC API ───────────────────────────────────────────────────────────────

def format_analysis(raw_text: str, lang: str = "ru", extra_data: dict | None = None) -> str:
    """Route to video or photo formatter based on GPT response format."""
    if _is_video_format(raw_text):
        result = _format_video(raw_text, lang, extra_data)
        # Safety: if formatter produced almost nothing, use fallback
        if len(result) < 80:
            return _fallback_video(raw_text, lang)
        return result
    return _format_photo(raw_text, lang, extra_data)


def _fallback_video(raw_text: str, lang: str) -> str:
    """Emergency fallback: extract ИТОГ/SUMMARY + score from raw GPT text."""
    # Try to find ИТОГ/SUMMARY section
    m = re.search(
        r'═{2,}\s*(?:ИТОГ|SUMMARY)\s*═{2,}\s*\n(.*?)(?=═{2,}|$)',
        raw_text, re.DOTALL,
    )
    if m:
        summary = m.group(1).strip()[:500]
    else:
        # Last resort: strip frame-by-frame analysis and truncate
        stripped = re.sub(
            r'═{2,}\s*(?:АНАЛИЗ КАДРОВ|FRAME.BY.FRAME|ГОНОЧНЫЙ РАЗБОР|RACE BREAKDOWN'
            r'|ТЕХНИЧЕСКИЙ РАЗБОР|TECHNICAL BREAKDOWN).*',
            '', raw_text, flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        summary = stripped[:2000] if stripped else raw_text[:2000]

    title = "Ski Technique Analysis" if lang == "en" else "Анализ техники"
    return f"🏔 {title}\n\n{summary}"
