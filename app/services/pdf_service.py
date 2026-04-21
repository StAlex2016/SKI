import os
import re
from datetime import datetime
from html import escape
from playwright.async_api import async_playwright

BOT_URL = "https://t.me/alpineski_bot"
BOT_HANDLE = "@alpineski_bot"


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _clean_line(line: str) -> str:
    for ch in ["🏔","👤","🎂","🏷","📊","⚖️","✅","⚠️","🎯","📈","🔹","📷"]:
        line = line.replace(ch, "")
    # Strip green and red confidence-style circle emojis (keep orange/yellow for rank markers)
    for ch in ["🟢","🟩","🔴","🟥"]:
        line = line.replace(ch, "")
    line = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
    line = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", line)
    line = re.sub(r"^#{1,4}\s*", "", line)
    line = re.sub(r"^[-=]{3,}$", "", line)
    line = line.replace("—", "-")
    return re.sub(r"\s+", " ", line).strip()


def _clean_markers(text: str, keep_leading: str = "") -> str:
    """Remove marker artifacts: empty parens, stray colored circles, duplicates.
    keep_leading: single emoji allowed at very start (e.g. "🟠" or "🟡")."""
    # Remove parenthesized orphan markers anywhere: (🟢) (🟡) (🟠) (🔴) ()
    text = re.sub(r"\s*\(\s*[🟢🟡🟠🔴🟩🟥]?\s*\)", "", text)
    severity_re = re.compile(r"[🟢🟡🟠🔴🟩🟥]")
    if keep_leading and text.startswith(keep_leading):
        rest = text[len(keep_leading):]
        rest = severity_re.sub("", rest)
        text = keep_leading + rest
    else:
        text = severity_re.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _trunc(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n-1]
    last_space = cut.rfind(" ")
    if last_space > int(n * 0.7):
        cut = cut[:last_space]
    return cut.rstrip(",-— ") + "…"


# ── PARSER ─────────────────────────────────────────────────────────────────────

def _parse(text: str, lang: str = "ru") -> dict:
    d = {
        "title": "Technique Analysis" if lang == "en" else "Анализ техники",
        "discipline": "GS",
        "athlete": "-", "birth_year": "-",
        "category": "-", "score": "-",
        "details": [],
        "strengths": [], "weaknesses": [], "recommendations": [],
        "potential": "-",
    }
    section = None

    # ключевые слова для обоих языков
    kw = {
        "title":   ["Анализ техники", "Technique Analysis", "Analysis"],
        "name":    ["Имя:", "Name:"],
        "year":    ["Год рождения:", "Year of birth:", "Birth year:"],
        "cat":     ["Категория:", "Category:"],
        "disc":    ["Дисциплина:", "Discipline:"],
        "score":   ["Общая оценка:", "Overall score:", "Overall:"],
        "str":     ["Сильные стороны", "Strengths", "Strong"],
        "weak":    ["Зоны роста", "Ошибки", "Areas", "Weaknesses", "Growth"],
        "rec":     ["Рекомендации", "Recommendations", "Упражнения", "Drills"],
        "pot":     ["Потенциал", "Potential"],
    }

    def match(line, keys):
        # Only match if keyword is at the start of the line (section headers)
        # or if the whole line is essentially just the keyword + optional colon
        ll = line.lower().strip()
        for k in keys:
            kl = k.lower()
            if ll.startswith(kl):
                return True
            # Allow matches like === Strengths === or # Strengths
            stripped = ll.lstrip('#*=═')
            if stripped.startswith(kl):
                return True
        return False

    for raw in text.splitlines():
        line = _clean_line(raw)
        if not line:
            continue

        if match(line, kw["title"]):
            d["title"] = _trunc(line, 50)
            lu = line.upper()
            d["discipline"] = "SL" if " SL" in lu or lu.endswith("SL") else "GS"
        elif any(line.startswith(k) for k in kw["name"]):
            for k in kw["name"]:
                line = line.replace(k, "")
            d["athlete"] = _trunc(line.strip(), 30)
        elif any(line.startswith(k) for k in kw["year"]):
            for k in kw["year"]:
                line = line.replace(k, "")
            d["birth_year"] = _trunc(line.strip(), 10)
        elif any(line.startswith(k) for k in kw["cat"]):
            for k in kw["cat"]:
                line = line.replace(k, "")
            d["category"] = _trunc(line.strip(), 10)
        elif any(line.startswith(k) for k in kw["disc"]):
            for k in kw["disc"]:
                line = line.replace(k, "")
            val = line.strip().upper()
            d["discipline"] = "SL" if "SL" in val else "GS"
        elif match(line, kw["score"]):
            # безопасный поиск числа после двоеточия
            m = re.search(r"[\d.]+\s*/\s*10", line)
            if m:
                d["score"] = m.group().replace(" ", "")
            else:
                m2 = re.search(r"[\d.]+", line.split(":")[-1])
                if m2:
                    d["score"] = m2.group() + "/10"
        elif match(line, kw["str"]):
            section = "strengths"
        elif match(line, kw["weak"]):
            section = "weaknesses"
        elif match(line, kw["rec"]):
            section = "recommendations"
        elif match(line, kw["pot"]):
            section = "potential"
            # capture inline text after colon, e.g. "Potential:Shows strong promise..."
            if ":" in line:
                inline = line.split(":", 1)[1].strip()
                if inline and d["potential"] == "-":
                    if "не хватает" not in inline.lower() and "missing" not in inline.lower():
                        d["potential"] = inline
        elif ("/" in line or " - " in line) and ("/10" in line or line.strip().endswith("—")) and len(d["details"]) < 5:
            for sep in (" - ", "—", "–"):
                if sep in line:
                    pts = line.rsplit(sep, 1)
                    val = pts[1].strip() if len(pts) > 1 else "—"
                    d["details"].append((_trunc(pts[0].strip(), 22), _trunc(val, 8)))
                    break
        # Structured drill fields (▸ Что делать: / ▸ Action: etc.)
        elif line.startswith("▸") and section == "recommendations":
            if d["recommendations"]:
                d["recommendations"][-1] += "\n" + line
        # Numbered drill items (1. Name)
        elif re.match(r"^\d+[\.\)]\s+", line) and section == "recommendations":
            item = re.sub(r"^\d+[\.\)]\s+", "", line).strip()
            if item and len(d["recommendations"]) < 3:
                d["recommendations"].append(item)
        elif re.match(r"^[•\-·]", line):
            item = re.sub(r"^[•\-·]\s*", "", line).strip()
            if not item:
                continue
            if section == "strengths" and len(d["strengths"]) < 3:
                d["strengths"].append(_clean_markers(item, keep_leading=""))
            elif section == "weaknesses" and len(d["weaknesses"]) < 3:
                idx = len(d["weaknesses"])
                expected = "🟠" if idx == 0 else ("🟡" if idx == 1 else "")
                if expected and item.startswith(expected):
                    cleaned = _clean_markers(item, keep_leading=expected)
                else:
                    cleaned = _clean_markers(item, keep_leading="")
                    if expected:
                        cleaned = f"{expected} {cleaned}"
                d["weaknesses"].append(cleaned)
            elif section == "recommendations" and len(d["recommendations"]) < 3:
                d["recommendations"].append(item)
            elif section == "potential":
                if "не хватает" not in item.lower() and "missing" not in item.lower():
                    if d["potential"] == "-":
                        d["potential"] = item
                    else:
                        d["potential"] += "\n" + item
        else:
            if section == "potential":
                if "не хватает" not in line.lower() and "missing" not in line.lower():
                    if d["potential"] == "-":
                        d["potential"] = line
                    else:
                        d["potential"] += "\n" + line

    while len(d["details"]) < 5:
        d["details"].append(("-", "-"))

    return d


def _score_color(s: str) -> str:
    try:
        v = float(re.search(r"[\d.]+", s).group())
        return "#1a6b3a" if v >= 8 else "#8b1a1a" if v < 6 else "#185fa5"
    except Exception:
        return "#185fa5"


def _score_pct(s: str) -> int:
    try:
        return int(float(re.search(r"[\d.]+", s).group()) * 10)
    except Exception:
        return 50


def _score_num(s: str) -> str:
    try:
        return str(int(float(re.search(r"[\d.]+", s).group())))
    except Exception:
        return s


# ── BUILD HTML ─────────────────────────────────────────────────────────────────

def build_html(text: str, lang: str = "ru", run_date: str = None, report_date: str = None) -> str:
    d = _parse(text, lang)
    # Compute run_date / report_date display strings (dd.mm.YYYY)
    today_iso = datetime.now().date().isoformat()
    _rd = run_date or today_iso
    _repd = report_date or today_iso
    def _fmt_d(iso):
        try:
            return iso[8:10] + "." + iso[5:7] + "." + iso[0:4]
        except Exception:
            return iso
    run_date_str = _fmt_d(_rd)
    report_date_str = _fmt_d(_repd)
    date_str = run_date_str  # Backward-compat alias

    sc = _score_color(d["score"])
    pct = _score_pct(d["score"])
    num = _score_num(d["score"])

    # локализация UI-строк
    if lang == "en":
        lbl_report    = "Alpine Ski Performance Lab · Technique Report"
        lbl_athlete   = "Athlete"
        lbl_born      = "Born"
        lbl_category  = "Category"
        lbl_disc      = "Discipline"
        lbl_run_date  = "Run date"
        lbl_report_short = "Report"
        lbl_overall   = "Overall score"
        lbl_strengths = "Strengths"
        lbl_weaknesses= "Areas for growth"
        lbl_recs      = "Recommendations"
        lbl_potential = "Potential"
        lbl_free      = "Alpine Ski Performance Lab · Free photo analysis"
        lbl_pro       = f'<a href="{BOT_URL}" style="color:#185fa5;text-decoration:none;">{BOT_HANDLE} →</a>' 
    else:
        lbl_report    = "Alpine Ski Performance Lab · Technique Report"
        lbl_athlete   = "Спортсмен"
        lbl_born      = "Год рождения"
        lbl_category  = "Категория"
        lbl_disc      = "Дисциплина"
        lbl_run_date  = "Заезд"
        lbl_report_short = "Отчёт"
        lbl_overall   = "Итоговая оценка"
        lbl_strengths = "Сильные стороны"
        lbl_weaknesses= "Зоны роста"
        lbl_recs      = "Рекомендации"
        lbl_potential = "Потенциал"
        lbl_free      = "Alpine Ski Performance Lab · Бесплатный анализ по фото"
        lbl_pro       = f'<a href="{BOT_URL}" style="color:#185fa5;text-decoration:none;">{BOT_HANDLE} →</a>' 

    # KPI cards
    kpi_html = ""
    for label, value in d["details"]:
        lbl = (label or "").strip()
        val = (value or "").strip()
        if val in ("—", "-", "–", ""):
            # Placeholder card — avoid double dashes: hide if label also empty
            if lbl in ("—", "-", "–", ""):
                kpi_html += f"""
        <div class="kc">
          <div class="kl">&nbsp;</div>
          <div class="kv"><span class="knum">&nbsp;</span></div>
        </div>"""
            else:
                kpi_html += f"""
        <div class="kc">
          <div class="kl">{escape(lbl)}</div>
          <div class="kv"><span class="knum">&mdash;</span></div>
        </div>"""
        else:
            num_part = value.split("/")[0].strip() if "/" in value else value
            den_part = value.split("/")[1].strip() if "/" in value else "10"
            kpi_html += f"""
        <div class="kc">
          <div class="kl">{escape(label)}</div>
          <div class="kv"><span class="knum">{escape(num_part)}</span><span class="kmax">/{escape(den_part)}</span></div>
        </div>"""

    # list items
    def li_items(items, cls):
        if not items:
            return f'<li class="{cls}">-</li>'
        return "".join(f'<li class="{cls}">{escape(_trunc(i, 125))}</li>' for i in items)

    # rec items — support structured drill fields (▸ lines)
    rec_html = ""
    for i, r in enumerate(d["recommendations"][:3], 1):
        # Check for structured fields
        lines_r = r.split("\n")
        main_text = escape(_trunc(lines_r[0], 90))
        fields_html = ""
        for sub_line in lines_r[1:]:
            sub_line = sub_line.strip()
            if sub_line.startswith("▸"):
                fields_html += f'<div style="font-size:10.5px;color:#5a7a99;margin-top:0;padding-left:4px;">{escape(sub_line)}</div>'
        rec_html += f"""
        <div class="ri">
          <span class="rn">{i:02d}</span>
          <span class="rt">{main_text}{fields_html}</span>
        </div>"""
    if not rec_html:
        rec_html = '<div class="ri"><span class="rn">01</span><span class="rt">-</span></div>'

    # Score HTML: omit /10 when score is missing/invalid
    if num in ("-", "—", "–", ""):
        score_html_inner = '<span class="score-big">—</span>'
    else:
        score_html_inner = f'<span class="score-big">{num}</span><span class="score-denom">/10</span>'

    # Recommendations block: hide entirely if nothing to show
    _has_real_recs = bool(d["recommendations"])
    if _has_real_recs:
        rec_block_html = (
            f'<div class="rec">'
            f'<div class="plbl">{lbl_recs}</div>'
            f'{rec_html}'
            f'</div>'
        )
    else:
        rec_block_html = ""

    # Potential block: hide entirely if no content (avoid lonely dash)
    _pot = (d["potential"] or "").strip()
    if _pot in ("-", "—", "–", "", "—"):
        pot_block_html = ""
    else:
        # Multi-line potential: each line on its own row
        _pot_lines = [l.strip() for l in _pot.split("\n") if l.strip()]
        # Clamp each line individually to keep block compact
        _pot_lines = [_trunc(l, 120) for l in _pot_lines[:3]]
        _pot_text = "<br/>".join(escape(l) for l in _pot_lines)
        pot_block_html = (
            f'<div class="pot">'
            f'<span class="ptag">{lbl_potential}</span>'
            f'<span class="ptxt">{_pot_text}</span>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="UTF-8"/>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
@page{{size:A5;margin:0}}
body{{
  font-family:Arial,Helvetica,sans-serif;
  background:#fff;
  color:#0a2540;
  -webkit-print-color-adjust:exact;
  print-color-adjust:exact;
  width:148mm;
  height:210mm;
  display:flex;
  flex-direction:column;
  overflow:hidden;
}}

.hdr{{background:#0a2540;display:flex;flex-direction:column}}
.hdr-top{{padding:10px 16px 8px}}
.eyebrow{{font-size:7px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#7a9bbf;margin-bottom:4px}}
.htitle{{font-size:20px;font-weight:900;color:#fff;letter-spacing:-0.5px;line-height:1.1}}
.free-badge{{font-size:6px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:rgba(255,255,255,0.5);border:0.5px solid rgba(255,255,255,0.2);padding:2px 7px;border-radius:3px;margin-left:8px;vertical-align:middle}}
.score-strip{{background:rgba(255,255,255,0.07);border-top:0.5px solid rgba(255,255,255,0.1);padding:8px 16px;display:flex;align-items:center;gap:18px}}
.score-main{{display:flex;align-items:baseline;gap:3px;flex-shrink:0}}
.score-big{{font-size:36px;font-weight:900;color:#fff;line-height:1;letter-spacing:-2px}}
.score-denom{{font-size:16px;font-weight:500;color:rgba(255,255,255,0.35)}}
.score-right{{flex:1}}
.score-lbl{{font-size:6.5px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#7a9bbf;display:block;margin-bottom:8px}}
.bar-bg{{height:8px;background:rgba(255,255,255,0.15);border-radius:4px}}
.bar-fill{{height:8px;background:{sc};border-radius:4px;width:{pct}%;box-shadow:0 0 8px {sc}80}}

.meta{{background:#f0f4f8;border-bottom:3px solid #0a2540;padding:0 14px;display:flex;height:32px}}
.mc{{flex:1;border-right:1px solid #c8d5e3;padding-right:8px;margin-right:8px;display:flex;flex-direction:column;justify-content:center}}
.mc:last-child{{border-right:none;padding-right:0;margin-right:0}}
.mk{{font-size:7px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#5a7a99;display:block;margin-bottom:2px;white-space:nowrap}}
.mv{{font-size:11px;font-weight:700;color:#0a2540;display:block;white-space:nowrap}}

.kpi{{display:flex;border-bottom:1px solid #e2e8f0;margin-top:4px}}
.kc{{flex:1;padding:7px 9px 8px;border-right:1px solid #e2e8f0;display:flex;flex-direction:column;justify-content:space-between}}
.kc:last-child{{border-right:none}}
.kl{{font-size:7.5px;font-weight:600;text-transform:uppercase;letter-spacing:0.7px;color:#7a9bbf;min-height:16px;line-height:1.2}}
.kv{{line-height:1;margin-top:5px}}
.knum{{font-size:22px;font-weight:900;color:#0a2540;letter-spacing:-1px}}
.kmax{{font-size:11px;font-weight:500;color:#b0bfce}}

.two{{display:flex;border-bottom:1px solid #e2e8f0;gap:10px}}
.col{{flex:1;padding:10px 12px}}
.col:first-child{{border-right:1px solid #c8d5e3}}
.plbl{{font-size:9.5px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#0a2540;border-bottom:2px solid #0a2540;padding-bottom:4px;margin-bottom:8px}}
.col ul{{list-style:none}}
.col ul li{{font-size:11.5px;line-height:1.3;color:#1e3a55;padding:3px 0 3px 11px;border-bottom:1px solid #f4f6f9;position:relative}}
.col ul li:last-child{{border-bottom:none}}
.col ul li::before{{content:'';position:absolute;left:0;top:9px;width:5px;height:2px}}
.str li::before{{background:#1a6b3a}}
.wk li::before{{background:#c0560a;width:3px}}

.rec{{padding:6px 14px;border-bottom:1px solid #e2e8f0}}
.rec .plbl{{font-size:9.5px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#0a2540;border-bottom:2px solid #0a2540;padding-bottom:4px;margin-bottom:8px}}
.ri{{display:flex;align-items:baseline;gap:10px;padding:2px 0;border-bottom:1px solid #f4f6f9}}
.ri:last-child{{border-bottom:none}}
.rn{{font-size:19px;font-weight:900;color:#dde5ef;line-height:1;min-width:24px;letter-spacing:-1px}}
.rt{{font-size:11.5px;line-height:1.3;color:#1e3a55;flex:1}}

.pot{{background:#0a2540;padding:8px 14px;display:flex;align-items:center;gap:10px;margin-top:0}}
.ptag{{font-size:7.5px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#7a9bbf;white-space:nowrap}}
.ptxt{{font-size:11.5px;color:#e8f0f8;font-weight:500;line-height:1.4}}

.spacer{{flex:1}}

.ftr{{background:#f0f4f8;padding:6px 14px;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #d0d5dd}}
.fl{{font-size:7.5px;color:#7a9bbf;font-weight:600}}
.fr{{font-size:7.5px;color:#0a2540;font-weight:700}}
.sep{{color:#c8d5e3;margin:0 4px}}
</style>
</head><body>

<div class="hdr">
  <div class="hdr-top">
    <div class="eyebrow">{lbl_report}</div>
    <div class="htitle">{escape(d["title"])} <span class="free-badge">FREE</span></div>
  </div>
  <div class="score-strip">
    <div class="score-main">
      {score_html_inner}
    </div>
    <div class="score-right">
      <span class="score-lbl">{lbl_overall}</span>
      <div class="bar-bg"><div class="bar-fill"></div></div>
    </div>
  </div>
</div>

<div class="meta">
  <div class="mc"><span class="mk">{lbl_athlete}</span><span class="mv">{escape(d["athlete"])}</span></div>
  <div class="mc"><span class="mk">{lbl_born}</span><span class="mv">{escape(d["birth_year"])}</span></div>
  <div class="mc"><span class="mk">{lbl_category}</span><span class="mv">{escape(d["category"])}</span></div>
  <div class="mc"><span class="mk">{lbl_disc}</span><span class="mv">{escape(d["discipline"])}</span></div>
  <div class="mc"><span class="mk">{lbl_run_date}</span><span class="mv">{run_date_str}</span></div>
  <div class="mc"><span class="mk">{lbl_report_short}</span><span class="mv">{report_date_str}</span></div>
</div>

<div class="kpi">{kpi_html}</div>

<div class="two">
  <div class="col">
    <div class="plbl">{lbl_strengths}</div>
    <ul>{li_items(d["strengths"], "str")}</ul>
  </div>
  <div class="col">
    <div class="plbl">{lbl_weaknesses}</div>
    <ul>{li_items(d["weaknesses"], "wk")}</ul>
  </div>
</div>

{rec_block_html}

{pot_block_html}

<div class="spacer"></div>

<div class="ftr">
  <span class="fl">{lbl_free}</span>
  <span class="fr">{lbl_pro}</span>
</div>

</body></html>"""


# ── GENERATE ───────────────────────────────────────────────────────────────────

async def generate_pdf(user_id: int, text: str, lang: str = "ru", run_date: str = None, report_date: str = None) -> str:
    html = build_html(text, lang, run_date=run_date, report_date=report_date)
    filename = os.path.join(os.getenv("OUTPUT_DIR", "."), f"report_{user_id}.pdf")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 559, "height": 794})
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(
            path=filename,
            format="A5",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        await browser.close()

    return filename
