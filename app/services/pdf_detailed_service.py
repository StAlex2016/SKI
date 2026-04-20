"""
Detailed video-analysis PDF — A4 (595x842 px).
3 pages: Overview | Phase breakdown + Speed loss | Drills + Potential
"""

import math
import os
import re
import time
from datetime import datetime
from html import escape

from playwright.async_api import async_playwright

from app.utils.text_utils import _clamp


# ── BRANDING ──────────────────────────────────────────────────────────────────
BOT_URL = "https://t.me/alpineski_bot"
BOT_HANDLE = "@alpineski_bot"


# ── COLORS ────────────────────────────────────────────────────────────────────
NAVY     = "#0a2540"
GREEN    = "#1D9E75";  GREEN_L  = "#eaf3de";  GREEN_D  = "#27500a"
ORANGE   = "#EF9F27";  ORANGE_L = "#faece7";  ORANGE_D = "#712b13"
RED      = "#E24B4A"
BLUE     = "#185fa5";  BLUE_L   = "#e6f1fb";  BLUE_D   = "#042c53"
BODY     = "#1e3a55";  MUTED    = "#5a7a99";  LABEL_C  = "#7a9bbf"


# ── I18N LABELS ──────────────────────────────────────────────────────────────
_I18N = {
    "ru": {
        "run_race": "Гонка", "run_training": "Тренировка",
        "cat_race": "Гоночная категория", "cat_training": "Тренировочная категория",
        "overall": "Общий балл",
        "video_analysis": "Анализ видео",
        "strengths": "Сильные стороны", "weaknesses": "Зоны роста",
        "radar_title": "Технический профиль",
        "phases_title": "Фазы поворота",
        "phase_title": "Разбор по фазам",
        "speed_loss_title": "Где теряется скорость",
        "speed_title": "Где теряется скорость",
        "priority": "Приоритет #1 - следующая тренировка",
        "drills": "Упражнения", "potential": "Потенциал",
        "ph_entry": "Вход", "ph_apex": "Апекс", "ph_exit": "Выход", "ph_transition": "Переход",
        "loss_min": "минимальная потеря", "loss_med": "средняя потеря", "loss_high": "высокая потеря",
        "eff_high": "высокая эффективность", "eff_med": "средняя эффективность",
        "loss_priority": "приоритет",
        "ph_at_entry": "входе", "ph_at_apex": "апексе", "ph_at_exit": "выходе", "ph_at_transition": "переходе",
        "bar_exit": "Выход из поворота", "bar_neutral": "Нейтральная фаза", "bar_apex": "Апекс (карвинг)",
        "bar_exit_sub": "Поздний вылет, ЦТ назад - основная потеря заезда",
        "bar_neutral_sub": "Время без давления на лыжи между воротами",
        "bar_apex_sub": "Чистый карвинг - нет потерь, лучшее место заезда",
        "speed_pct": "скорость", "tech_pct": "техника",
        "drill_action": "Что делать", "drill_focus": "Фокус", "drill_success": "Успех",
        "no_data": "Нет данных",
        "plan_title": "План тренировки",
        "limitations": "Ограничения анализа",
    },
    "en": {
        "run_race": "Race", "run_training": "Training",
        "cat_race": "Race category", "cat_training": "Training category",
        "overall": "Overall score",
        "video_analysis": "Video analysis",
        "strengths": "Strengths", "weaknesses": "Growth areas",
        "radar_title": "Technical profile",
        "phases_title": "Turn phases",
        "phase_title": "Phase breakdown",
        "speed_loss_title": "Where speed is lost",
        "speed_title": "Where speed is lost",
        "priority": "Priority #1 - next training",
        "drills": "Drills", "potential": "Potential",
        "ph_entry": "Entry", "ph_apex": "Apex", "ph_exit": "Exit", "ph_transition": "Transition",
        "loss_min": "minimal loss", "loss_med": "moderate loss", "loss_high": "high loss",
        "eff_high": "high efficiency", "eff_med": "moderate efficiency",
        "loss_priority": "priority",
        "ph_at_entry": "entry", "ph_at_apex": "apex", "ph_at_exit": "exit", "ph_at_transition": "transition",
        "bar_exit": "Turn exit", "bar_neutral": "Neutral phase", "bar_apex": "Apex (carving)",
        "bar_exit_sub": "Late exit, CoM back - main speed loss",
        "bar_neutral_sub": "Time without ski pressure between gates",
        "bar_apex_sub": "Clean carving - no losses, best part of run",
        "speed_pct": "speed", "tech_pct": "technique",
        "drill_action": "Action", "drill_focus": "Focus", "drill_success": "Success",
        "no_data": "No data",
        "plan_title": "Training plan",
        "limitations": "Analysis limitations",
    },
}

# === TEXT LIMITS (chars) — clamp data BEFORE rendering ===
_LIM = {
    "str_p1": 120,
    "weak_p1": 120,
    "phase_body": 160,
    "drill_name": 60,
    "drill_detail": 140,
    "potential": 160,
    "priority": 60,
}


# ── RADAR LABELS ─────────────────────────────────────────────────────────────

_RADAR_LABELS = {
    'ru': {'stance': 'Стойка', 'edge': 'Кантование', 'body': 'Корпус',
           'arms': 'Руки', 'line': 'Линия', 'balance': 'Баланс'},
    'en': {'stance': 'Stance', 'edge': 'Edging', 'body': 'Body',
           'arms': 'Arms', 'line': 'Line', 'balance': 'Balance'},
}

_RADAR_KEYS = ['stance', 'edge', 'body', 'arms', 'line', 'balance']


def _color_dot(color: str) -> str:
    _COLORS = {
        "green": "#4CAF50", "orange": "#FF9800", "red": "#F44336", "yellow": "#FFC107",
    }
    c = _COLORS.get(color, "#999")
    return (
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'border-radius:50%;background:{c};vertical-align:middle;"></span>'
    )


# ── TEXT HELPERS ──────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r'\(кадр[ы]?\s*[\d,\s\u2013\-]+\)', '', str(text))
    text = re.sub(r'\(frame[s]?\s*[\d,\s\u2013\-]+\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = text.replace(' — ', ' - ').replace('—', '-')
    return text.strip()


def _clean_confidence(text: str) -> str:
    """Strip confidence markers and emoji from text."""
    text = re.sub(r'\s*\([\U0001F534\U0001F7E1\U0001F7E2\U0001F7E0][^)]*\)\s*', '', text)
    text = re.sub(r'[\U0001F534\U0001F7E1\U0001F7E2\U0001F7E0]', '', text)
    text = re.sub(
        r'\s*\(?\.?\s*(?:Высокая|Средняя|Низкая|High|Medium|Low)\s+(?:уверенность|confidence)\.?\)?\s*',
        '', text, flags=re.IGNORECASE,
    )
    return text.strip().rstrip('.,;')


def _sc(v: float) -> str:
    """Score -> color."""
    return GREEN if v >= 8 else (ORANGE if v >= 6.5 else RED)


# ── WEIGHTS ───────────────────────────────────────────────────────────────────

def _weights(birth_year: str, run_type: str) -> tuple[int, int]:
    try:
        age = 2025 - int(birth_year)
    except Exception:
        age = 12
    race = run_type == "race"
    if age <= 8:   return (90, 10)  if not race else (80, 20)
    if age <= 10:  return (80, 20)  if not race else (65, 35)
    if age <= 12:  return (70, 30)  if not race else (55, 45)
    if age <= 14:  return (60, 40)  if not race else (40, 60)
    return         (50, 50)         if not race else (25, 75)


_PHASE_ORDER = ("Entry", "Apex", "Exit", "Transition")

_PHASE_RU = {"Entry": "ВХОД", "Apex": "АПЕКС", "Exit": "ВЫХОД", "Transition": "ПЕРЕХОД"}
_PHASE_DISP_RU = {"Entry": "Вход", "Apex": "Апекс", "Exit": "Выход", "Transition": "Переход"}
_PHASE_SUB_RU = {
    "Entry": "Вход / Подход", "Apex": "Апекс / Ворота",
    "Exit": "Выход / Освобождение", "Transition": "Переход / Смена канта",
}
_PHASE_DISP_EN = {"Entry": "Entry", "Apex": "Apex", "Exit": "Exit", "Transition": "Transition"}
_PHASE_SUB_EN = {
    "Entry": "Entry / Approach", "Apex": "Apex / Gate",
    "Exit": "Exit / Release", "Transition": "Transition / Edge change",
}
_PHASE_EN = {"Entry": "ENTRY", "Apex": "APEX", "Exit": "EXIT", "Transition": "TRANSITION"}


# ── RADAR SVG ─────────────────────────────────────────────────────────────────

def _build_radar_svg(radar: dict, lang: str) -> str:
    """Build an SVG hexagonal radar chart for 6 technique axes."""
    labels = _RADAR_LABELS.get(lang, _RADAR_LABELS['en'])
    values = [radar.get(k, 0) for k in _RADAR_KEYS]

    if not any(values):
        no_data = _I18N.get(lang, _I18N['en'])['no_data']
        return (
            f'<div style="width:100%;height:100%;display:flex;align-items:center;'
            f'justify-content:center;color:{MUTED};font-size:13px;">{no_data}</div>'
        )

    W, H = 230, 200
    cx, cy = W / 2, H / 2 - 2
    R = 68  # max radius
    n = 6
    rings = [0.25, 0.5, 0.75, 1.0]

    def polar(angle_idx: int, r: float) -> tuple[float, float]:
        a = math.radians(-90 + angle_idx * 360 / n)
        return cx + r * math.cos(a), cy + r * math.sin(a)

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;">'

    # Concentric rings
    for ring in rings:
        pts = " ".join(f"{polar(i, R * ring)[0]:.1f},{polar(i, R * ring)[1]:.1f}" for i in range(n))
        svg += f'<polygon points="{pts}" fill="none" stroke="#d0d5dd" stroke-width="0.5"/>'

    # Axis lines
    for i in range(n):
        x, y = polar(i, R)
        svg += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d0d5dd" stroke-width="0.5"/>'

    # Data polygon
    data_pts = []
    for i, v in enumerate(values):
        frac = max(0, min(v, 10)) / 10.0
        data_pts.append(polar(i, R * frac))
    pts_str = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in data_pts)
    svg += f'<polygon points="{pts_str}" fill="rgba(29,158,117,0.18)" stroke="{GREEN}" stroke-width="2"/>'

    # Data points
    for px, py in data_pts:
        svg += f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{GREEN}" stroke="#fff" stroke-width="1.5"/>'

    # Labels + values
    label_r = R + 18
    for i, key in enumerate(_RADAR_KEYS):
        lx, ly = polar(i, label_r)
        lbl = labels[key]
        v = values[i]
        anchor = "middle"
        if i == 1 or i == 2:
            anchor = "start"
        elif i == 4 or i == 5:
            anchor = "end"

        svg += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="10" font-family="Arial" fill="{BODY}" font-weight="600">{lbl}</text>'
        )
        svg += (
            f'<text x="{lx:.1f}" y="{ly + 12:.1f}" text-anchor="{anchor}" '
            f'font-size="11" font-family="Arial" fill="{GREEN}" font-weight="700">{v}</text>'
        )

    svg += '</svg>'
    return svg


# ── HTML BUILDER ──────────────────────────────────────────────────────────────

def build_html_detailed(data: dict, lang: str) -> str:  # noqa: C901

    L = _I18N.get(lang, _I18N["ru"])

    # ── Data extraction ────────────────────────────────────────────────────
    athlete    = _clean(str(data.get("athlete",    "-")))
    birth_year = str(data.get("birth_year", "2012"))
    category   = _clean(str(data.get("category",   "-")))
    discipline = _clean(str(data.get("discipline", "GS")))
    run_type   = str(data.get("run_type", "training"))
    ph_scores  = data.get("phase_scores", {})
    radar      = data.get("radar", {})
    strengths  = [_clean(str(s.get("text", s) if isinstance(s, dict) else s))
                  for s in data.get("strengths", [])]
    weaknesses = [_clean_confidence(_clean(str(s.get("text", s) if isinstance(s, dict) else s)))
                  for s in data.get("weaknesses", [])]
    phases_raw = data.get("phases", [])
    drills_raw = data.get("drills", [])
    potential_raw = _clean(str(data.get("potential", "-")))
    potential = "\n".join(
        _clamp(line, _LIM["potential"], "sentence")
        for line in potential_raw.split("\n") if line.strip()
    )
    # date_str = report date (when PDF is generated); run_date = when skiing happened
    _report_iso = str(data.get("report_date") or datetime.now().date().isoformat())
    _run_iso    = str(data.get("run_date") or _report_iso)
    def _fmt_d(iso):
        try:
            return iso[8:10] + "." + iso[5:7] + "." + iso[0:4]
        except Exception:
            return iso
    date_str       = _fmt_d(_report_iso)   # footer keeps report date
    run_date_str   = _fmt_d(_run_iso)
    report_date_str = date_str

    try:
        score_val = float(str(data.get("score", "7.5")).replace("/10", "").strip())
    except Exception:
        score_val = 7.5
    score_pct = max(0, min(100, int(round(score_val * 10))))

    # Normalize phases
    _ph_by_type = {str(p.get("phase", "")): p for p in phases_raw}
    phases: list[dict | None] = [_ph_by_type.get(pt) for pt in _PHASE_ORDER]

    if phases[2] is None:
        exit_obs = weaknesses[0] if weaknesses else "-"
        try:
            exit_sc_val = float(ph_scores.get("exit", 7.5))
        except Exception:
            exit_sc_val = 7.5
        phases[2] = {
            "phase": "Exit", "score": exit_sc_val,
            "observation": exit_obs, "frame_path": None,
        }

    # Deduplicate drills
    seen: set[str] = set()
    drills: list[dict] = []
    for d in drills_raw:
        n = _clean(str(d.get("name", ""))).strip()
        if n and n not in seen:
            seen.add(n)
            drills.append({
                "name":        _clamp(n, _LIM["drill_name"], "word"),
                "description": _clean(str(d.get("description", ""))),
                "priority":    bool(d.get("priority", False)),
                "action":      _clamp(str(d.get("action", "")).strip(), _LIM["drill_detail"], "sentence"),
                "focus":       _clamp(str(d.get("focus", "")).strip(), _LIM["drill_detail"], "sentence"),
                "success":     _clamp(str(d.get("success", "")).strip(), _LIM["drill_detail"], "sentence"),
            })

    lbl_run = L["run_race"] if run_type == "race" else L["run_training"]
    tw, sw  = _weights(birth_year, run_type)
    cat_lbl = L["cat_race"] if run_type == "race" else L["cat_training"]

    # Phase scores
    ph_flt: dict[str, float] = {}
    for k, v in ph_scores.items():
        try:
            ph_flt[k] = float(v)
        except Exception:
            pass

    entry_v = ph_flt.get("entry",      7.5)
    apex_v  = ph_flt.get("apex",       7.5)
    exit_v  = ph_flt.get("exit",       7.5)
    trans_v = ph_flt.get("transition", 7.5)

    # Speed loss calculations
    exit_loss    = round((10 - exit_v)  * 8)
    neutral_loss = round((10 - trans_v) * 5)
    apex_eff     = round(apex_v * 10)

    def _loss_label(score: float) -> tuple[str, str]:
        if score >= 8.5:
            return (L["loss_min"], "#4CAF50")
        if score >= 7.0:
            return (L["loss_med"], "#FF9800")
        return (L["loss_high"], "#F44336")

    def _loss_dot(score: float) -> str:
        if score >= 8.5: return _color_dot("green")
        if score >= 7.0: return _color_dot("orange")
        return _color_dot("red")

    def _eff_label(score: float) -> tuple[str, str]:
        if score >= 8.5:
            return (L["eff_high"], "#4CAF50")
        return (L["eff_med"], "#FF9800")

    def _badge_bg(v: float) -> tuple[str, str]:
        if v >= 8:   return ("#eaf3de", "#27500a")
        if v >= 7:   return ("#faeeda", "#412402")
        return ("#faece7", "#712b13")

    _PHASE_AT = {
        "entry": L["ph_at_entry"], "apex": L["ph_at_apex"],
        "exit": L["ph_at_exit"], "transition": L["ph_at_transition"],
    }

    def _auto_issue(phase_type: str, score: float) -> str:
        dot = _loss_dot(score)
        lbl, _ = _loss_label(score)
        if score < 7.0:
            return f"{dot} {lbl} - {L['loss_priority']}"
        return f"{dot} {lbl}"

    def _speed_box(col: str, text: str) -> str:
        bg = "#f0faf0" if col == GREEN else "#fff5f0"
        return (
            f'<div style="font-size:11px;color:{col};background:{bg};'
            f'padding:4px 8px;border-left:3px solid {col};line-height:1.3;overflow:hidden;">'
            f'{text}</div>'
        )

    # ════════════════════════════════════════════════════════════ PAGE 1

    topbar = (
        f'<div style="height:32px;padding:0 20px;border-bottom:0.5px solid #e2e8f0;'
        f'display:flex;justify-content:space-between;align-items:center;flex-shrink:0;">'
        f'<span style="font-size:10px;font-weight:700;color:{NAVY};letter-spacing:0.5px;">ALPINE SKI PERFORMANCE LAB</span>'
        f'<span style="font-size:11px;color:{LABEL_C};">{L["video_analysis"]} &middot; {escape(discipline)} {escape(lbl_run)}</span>'
        f'<span style="background:{NAVY};color:#fff;font-size:11px;padding:3px 9px;border-radius:3px;font-weight:700;">PRO</span>'
        f'</div>'
    )

    def footer_bar(n: int, mt: bool = False) -> str:
        mt_s = "margin-top:auto;" if mt else ""
        return (
            f'<div style="height:22px;background:#f0f4f8;padding:0 16px;{mt_s}'
            f'display:flex;justify-content:space-between;align-items:center;'
            f'border-top:0.5px solid #d0d5dd;flex-shrink:0;">'
            f'<span style="font-size:11px;color:{LABEL_C};">'
            f'Alpine Ski Performance Lab PRO &middot; '
            f'<a href="{BOT_URL}" style="color:{BLUE};text-decoration:none;">{BOT_HANDLE}</a>'
            f' &middot; {escape(date_str)}</span>'
            f'<span style="font-size:11px;color:{NAVY};font-weight:700;">{n} / 3</span>'
            f'</div>'
        )

    run_pill = (
        'background:rgba(192,86,10,0.5);color:#ffcba0;'
        if run_type == "race" else
        'background:rgba(255,255,255,0.12);color:#b8d0e8;'
    )
    hero = (
        f'<div style="height:170px;background:{NAVY};padding:14px 20px;flex-shrink:0;'
        f'display:flex;flex-direction:column;justify-content:space-between;">'
        f'<div>'
        f'<div style="font-size:42px;font-weight:900;color:#fff;line-height:1;'
        f'letter-spacing:-1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
        f'{escape(athlete)}</div>'
        f'<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;">'
        f'<span style="background:rgba(255,255,255,0.12);color:#b8d0e8;font-size:11px;'
        f'padding:3px 10px;border-radius:10px;">{escape(birth_year)}</span>'
        f'<span style="background:rgba(24,95,165,0.4);color:#b5d4f4;font-size:11px;'
        f'padding:3px 10px;border-radius:10px;">{escape(category)}</span>'
        f'<span style="background:rgba(255,255,255,0.12);color:#b8d0e8;font-size:11px;'
        f'padding:3px 10px;border-radius:10px;">{escape(discipline)}</span>'
        f'<span style="{run_pill}font-size:11px;padding:3px 10px;border-radius:10px;">'
        f'{escape(lbl_run)}</span>'
        f'<span style="background:rgba(255,255,255,0.12);color:#b8d0e8;font-size:11px;'
        f'padding:3px 10px;border-radius:10px;">📅 {run_date_str}</span>'
        f'<span style="background:rgba(255,255,255,0.07);color:#7a9bbf;font-size:10px;'
        f'padding:3px 10px;border-radius:10px;">{sw}% {L["speed_pct"]} &middot; {tw}% {L["tech_pct"]}</span>'
        f'</div></div>'
        f'<div style="display:flex;align-items:flex-end;gap:20px;">'
        f'<div>'
        f'<span style="font-size:58px;font-weight:900;color:#fff;line-height:1;letter-spacing:-2px;">{score_val:.1f}</span>'
        f'<span style="font-size:22px;color:rgba(255,255,255,0.35);">/10</span>'
        f'</div>'
        f'<div style="flex:1;padding-bottom:6px;">'
        f'<div style="font-size:10px;color:#7a9bbf;text-transform:uppercase;'
        f'letter-spacing:1px;margin-bottom:6px;">{L["overall"]} &middot; {cat_lbl}</div>'
        f'<div style="height:7px;background:rgba(255,255,255,0.15);border-radius:4px;">'
        f'<div style="height:7px;background:#fff;border-radius:4px;width:{score_pct}%;"></div>'
        f'</div></div>'
        f'</div>'
        f'</div>'
    )

    # ── MIDDLE: Radar + Phase cards ────────────────────────────────────────
    radar_svg = _build_radar_svg(radar, lang)

    # Phase cards 2x2
    kpi_items = [
        (L["ph_entry"], entry_v), (L["ph_apex"], apex_v),
        (L["ph_exit"], exit_v), (L["ph_transition"], trans_v),
    ]
    phase_cards_html = ""
    for i, (lbl, val) in enumerate(kpi_items):
        col = _sc(val)
        phase_cards_html += (
            f'<div style="flex:1;min-width:45%;background:#f8fafc;border-radius:6px;'
            f'padding:8px 10px;display:flex;flex-direction:column;align-items:center;'
            f'justify-content:center;">'
            f'<div style="font-size:10px;color:{LABEL_C};text-transform:uppercase;'
            f'letter-spacing:0.5px;margin-bottom:3px;">{lbl}</div>'
            f'<div style="font-size:24px;font-weight:900;color:{col};line-height:1;">{val:.1f}</div>'
            f'</div>'
        )

    middle_section = (
        f'<div style="display:flex;padding:10px 20px;gap:14px;flex-shrink:0;height:230px;margin-bottom:22px;">'
        # Left: radar
        f'<div style="flex:1;display:flex;flex-direction:column;">'
        f'<div style="font-size:12px;font-weight:700;color:{NAVY};text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-bottom:6px;">{L["radar_title"]}</div>'
        f'<div style="flex:1;display:flex;align-items:center;justify-content:center;">'
        f'{radar_svg}</div>'
        f'</div>'
        # Right: phase cards
        f'<div style="flex:1;display:flex;flex-direction:column;">'
        f'<div style="font-size:12px;font-weight:700;color:{NAVY};text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-bottom:6px;">{L["phases_title"]}</div>'
        f'<div style="flex:1;display:flex;flex-wrap:wrap;gap:6px;align-content:center;">'
        f'{phase_cards_html}</div>'
        f'</div>'
        f'</div>'
    )

    # ── BOTTOM: Strengths / Weaknesses ──────────────────────────────────────

    def _sw_items(items: list, is_str: bool) -> str:
        col = GREEN if is_str else ORANGE
        head = L["strengths"] if is_str else L["weaknesses"]
        display = items[:3]
        out = (
            f'<div style="flex:1;display:flex;flex-direction:column;overflow:hidden;">'
            f'<div style="padding:0 0 6px;margin-bottom:6px;border-bottom:2px solid {col};">'
            f'<span style="font-size:11px;font-weight:700;text-transform:uppercase;'
            f'color:{col};">{head}</span></div>'
        )
        if not display:
            out += f'<div style="color:#bbb;font-size:11px;">&mdash;</div>'
        else:
            for j, item in enumerate(display):
                out += (
                    f'<div style="padding:8px 8px;border-left:2px solid {col};'
                    f'margin-bottom:6px;overflow:hidden;">'
                    f'<div style="font-size:11px;color:{BODY};line-height:1.4;">'
                    f'{escape(item)}</div>'
                    f'</div>'
                )
        out += '</div>'
        return out

    sw_section = (
        f'<div style="flex:1;padding:6px 20px 4px;display:flex;gap:20px;overflow:hidden;">'
        + _sw_items(strengths, True)
        + _sw_items(weaknesses, False)
        + '</div>'
    )

    # ════════════════════════════════════════════════════════════ PAGE 2

    p2_header = (
        f'<div style="height:40px;background:{NAVY};padding:0 20px;'
        f'display:flex;justify-content:space-between;align-items:center;flex-shrink:0;">'
        f'<span style="font-size:18px;font-weight:900;color:#fff;">{L["phase_title"]}</span>'
        f'<span style="font-size:11px;color:{LABEL_C};">{escape(athlete)} &middot; '
        f'{escape(discipline)} &middot; '
        f'<span style="color:{GREEN};">T</span> {L["tech_pct"]} '
        f'<span style="color:{ORANGE};">S</span> {L["speed_pct"]}</span>'
        f'</div>'
    )

    # 4 phase cards (no photos)
    phase_blocks = ""
    for idx, ph in enumerate(phases):
        pt = _PHASE_ORDER[idx]
        ph_disp = _PHASE_DISP_EN[pt] if lang == "en" else _PHASE_DISP_RU[pt]
        ph_sub  = _PHASE_SUB_EN[pt] if lang == "en" else _PHASE_SUB_RU[pt]
        bb = "border-bottom:0.5px solid #e2e8f0;" if idx < 3 else ""

        if ph is None:
            ph_sc = ph_flt.get(pt.lower(), 7.5)
        else:
            try:
                ph_sc = float(ph.get("score", 7.5))
            except Exception:
                ph_sc = 7.5

        ph_obs = ""
        if ph is not None:
            ph_obs = _clamp(_clean(str(ph.get("observation", ""))), _LIM["phase_body"], "sentence")

        border_col = _sc(ph_sc)
        t_bg, t_fg = _badge_bg(ph_sc)
        t_sc = min(10.0, ph_sc + (sw / 100))
        s_sc = max(0.0, ph_sc - (sw / 100))

        # Issue text
        issue_text = _auto_issue(pt.lower(), ph_sc)

        phase_blocks += (
            f'<div style="{bb}padding:10px 20px;flex-shrink:0;'
            f'border-left:2px solid {border_col};overflow:hidden;">'
            f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;'
            f'color:{LABEL_C};margin-bottom:3px;">{escape(ph_sub)}</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
            f'<span style="font-size:16px;font-weight:900;color:{NAVY};">{escape(ph_disp)}</span>'
            f'<div style="display:flex;gap:5px;flex-shrink:0;">'
            f'<span style="background:{t_bg};color:{t_fg};font-size:12px;font-weight:900;'
            f'padding:3px 8px;border-radius:4px;">T {t_sc:.1f}</span>'
            f'<span style="background:{t_bg};color:{t_fg};font-size:12px;font-weight:900;'
            f'padding:3px 8px;border-radius:4px;">S {s_sc:.1f}</span>'
            f'</div></div>'
            f'<div style="font-size:11px;color:{BODY};line-height:1.4;margin-bottom:4px;'
            f'max-height:46px;overflow:hidden;">{escape(ph_obs)}</div>'
            f'<div style="font-size:10px;color:{border_col};">{issue_text}</div>'
            f'</div>'
        )

    # Speed loss bars
    def _speed_bar(label: str, val_txt: str, fill_pct: int, sub: str, good: bool,
                   last: bool = False) -> str:
        col = GREEN if good else ORANGE
        pct = max(2, min(95, fill_pct))
        mb  = "" if last else "margin-bottom:14px;"
        return (
            f'<div style="{mb}">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
            f'<span style="font-size:12px;color:{BODY};">{label}</span>'
            f'<span style="font-size:12px;font-weight:700;color:{col};display:flex;'
            f'align-items:center;gap:4px;">{val_txt}</span></div>'
            f'<div style="height:8px;background:#f0f4f8;border-radius:4px;">'
            f'<div style="height:8px;background:{col};border-radius:4px;width:{pct}%;"></div></div>'
            f'<div style="font-size:10px;color:{MUTED};margin-top:2px;">{sub}</div>'
            f'</div>'
        )

    exit_bar_w    = max(10, min(95, exit_loss    * 3))
    neutral_bar_w = max(10, min(95, neutral_loss * 3))
    apex_bar_w    = max(10, min(95, apex_eff))

    _exit_lbl, _   = _loss_label(exit_v)
    _neut_lbl, _   = _loss_label(trans_v)
    _apex_lbl, _   = _eff_label(apex_v)

    speed_bars = (
        f'<div style="padding:16px 20px;border-top:1px solid #e2e8f0;flex:none;margin-top:auto;">'
        f'<div style="font-size:12px;font-weight:700;color:{NAVY};text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-bottom:14px;">{L["speed_loss_title"]}</div>'
        + _speed_bar(L["bar_exit"],
                     f"{_loss_dot(exit_v)} {_exit_lbl}", exit_bar_w,
                     L["bar_exit_sub"], False)
        + _speed_bar(L["bar_neutral"],
                     f"{_loss_dot(trans_v)} {_neut_lbl}", neutral_bar_w,
                     L["bar_neutral_sub"], False)
        + _speed_bar(L["bar_apex"],
                     f"{_loss_dot(apex_v)} {_apex_lbl}", apex_bar_w,
                     L["bar_apex_sub"], True, last=True)
        + '</div>'
    )

    # ════════════════════════════════════════════════════════════ PAGE 3

    p3_header = (
        f'<div style="height:40px;background:{NAVY};padding:0 20px;'
        f'display:flex;align-items:center;flex-shrink:0;">'
        f'<span style="font-size:18px;font-weight:900;color:#fff;">'
        f'{L["plan_title"]} &middot; {escape(discipline)} {escape(lbl_run)}</span>'
        f'</div>'
    )

    priority_drill = next((d for d in drills if d.get("priority")), drills[0] if drills else None)
    if priority_drill:
        p_name = escape(_clamp(priority_drill["name"], _LIM["priority"], "word"))
    else:
        p_name = "-"

    priority_box = (
        f'<div style="background:{NAVY};margin:10px 16px 0;border-radius:6px;'
        f'padding:14px 18px;flex-shrink:0;overflow:hidden;">'
        f'<div style="font-size:10px;color:{LABEL_C};text-transform:uppercase;'
        f'letter-spacing:1px;margin-bottom:2px;">{L["priority"]}</div>'
        f'<div style="font-size:14px;font-weight:900;color:#fff;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{p_name}</div>'
        f'</div>'
    )

    drills_header = (
        f'<div style="padding:12px 16px 0;flex-shrink:0;">'
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:{NAVY};'
        f'border-bottom:2px solid {NAVY};padding-bottom:4px;">{L["drills"]}</div>'
        f'</div>'
    )

    drills_html = ""
    for i, d in enumerate(drills[:4]):
        name  = escape(d["name"])
        bl    = f"border-left:4px solid {ORANGE};" if i == 0 else ""

        action_txt  = d.get("action", "").strip()
        focus_txt   = d.get("focus", "").strip()
        success_txt = d.get("success", "").strip()

        if action_txt or focus_txt or success_txt:
            fields_html = ""
            if action_txt:
                fields_html += (
                    f'<div style="font-size:11px;color:{BODY};margin-top:2px;">'
                    f'<span style="color:{GREEN};font-weight:700;">&#9656; {L["drill_action"]}:</span> {escape(action_txt)}</div>'
                )
            if focus_txt:
                fields_html += (
                    f'<div style="font-size:11px;color:{BODY};margin-top:1px;">'
                    f'<span style="color:{BLUE};font-weight:700;">&#9656; {L["drill_focus"]}:</span> {escape(focus_txt)}</div>'
                )
            if success_txt:
                fields_html += (
                    f'<div style="font-size:11px;color:{BODY};margin-top:1px;">'
                    f'<span style="color:{ORANGE};font-weight:700;">&#9656; {L["drill_success"]}:</span> {escape(success_txt)}</div>'
                )
            drills_html += (
                f'<div style="padding:10px 12px;border-bottom:0.5px solid #f0f4f8;'
                f'display:flex;gap:10px;{bl}flex-shrink:0;overflow:hidden;">'
                f'<div style="font-size:18px;font-weight:900;color:#e8edf2;line-height:1;'
                f'min-width:24px;flex-shrink:0;padding-top:2px;">{i + 1:02d}</div>'
                f'<div style="flex:1;min-width:0;">'
                f'<div style="font-size:12px;font-weight:700;color:{NAVY};'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>'
                f'{fields_html}'
                f'</div></div>'
            )
        else:
            desc = d.get("description", "").strip() or d["name"]
            drills_html += (
                f'<div style="padding:10px 12px;border-bottom:0.5px solid #f0f4f8;'
                f'display:flex;gap:10px;{bl}flex-shrink:0;overflow:hidden;">'
                f'<div style="font-size:18px;font-weight:900;color:#e8edf2;line-height:1;'
                f'min-width:24px;flex-shrink:0;padding-top:2px;">{i + 1:02d}</div>'
                f'<div style="flex:1;min-width:0;">'
                f'<div style="font-size:12px;font-weight:700;color:{NAVY};'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>'
                f'<div style="font-size:11px;color:{BODY};margin-top:2px;line-height:1.4;">{escape(desc)}</div>'
                f'</div></div>'
            )

    # Potential + limitations
    potential_lines = [l.strip() for l in potential.split("\n") if l.strip()]
    limitation_line = ""
    main_potential = []
    for pl in potential_lines:
        pl_upper = pl.upper()
        if any(k in pl_upper for k in ["ОГРАНИЧЕНИ", "LIMITATION"]):
            limitation_line = pl
        else:
            main_potential.append(pl)

    potential_text = "<br>".join(escape(p) for p in main_potential) if main_potential else escape(potential)

    potential_box = (
        f'<div style="flex:none;margin:10px 16px 0;padding:10px 12px;'
        f'border-left:2px solid {BLUE};background:{BLUE_L};'
        f'border-radius:0 6px 6px 0;overflow:hidden;">'
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
        f'color:{BLUE};letter-spacing:1px;margin-bottom:6px;">{L["potential"]}</div>'
        f'<div style="font-size:11px;color:{BLUE_D};line-height:1.5;">{potential_text}</div>'
        f'</div>'
    )

    limitation_html = ""
    if limitation_line:
        limitation_html = (
            f'<div style="padding:6px 16px 4px;flex-shrink:0;">'
            f'<div style="font-size:10px;color:{MUTED};font-style:italic;line-height:1.3;">'
            f'{escape(limitation_line)}</div>'
            f'</div>'
        )

    # ════════════════════════════════════════════════════ ASSEMBLE

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
@page{{margin:0;}}
body{{font-family:Arial,Helvetica,sans-serif;margin:0;width:595px;
  -webkit-print-color-adjust:exact;print-color-adjust:exact;}}
.page{{width:595px;height:750px;background:#fff;page-break-after:always;
  overflow:hidden;display:flex;flex-direction:column;}}
.page:last-child{{page-break-after:avoid;}}
</style>
</head><body>

<!-- PAGE 1: Overview -->
<div class="page">
{topbar}
{hero}
{middle_section}
{sw_section}
{footer_bar(1, mt=True)}
</div>

<!-- PAGE 2: Phases + Speed loss -->
<div class="page">
{p2_header}
{phase_blocks}
{speed_bars}
{footer_bar(2, mt=True)}
</div>

<!-- PAGE 3: Plan -->
<div class="page">
{p3_header}
{priority_box}
{drills_header}
{drills_html}
{potential_box}
{limitation_html}
{footer_bar(3, mt=True)}
</div>

</body></html>"""


# ── PDF GENERATOR ─────────────────────────────────────────────────────────────

async def generate_pdf_detailed(user_id: int, data: dict, lang: str = "ru") -> str:
    """Render 3-page A4 analysis report -> PDF. Returns file path."""
    html     = build_html_detailed(data, lang)
    filename = f"report_detailed_{user_id}_{int(time.time())}.pdf"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page    = await browser.new_page(viewport={"width": 595, "height": 750})
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(
            path=filename,
            width="157.4mm",
            height="198.4mm",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        await browser.close()

    return filename
