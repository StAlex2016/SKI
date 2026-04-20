import asyncio
import os
import re
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)

from app.config import BOT_TOKEN, OPENAI_API_KEY
from app.db import init_db
from app.services.openai_service import analyze_images, check_images_quality
from app.services.pdf_service import generate_pdf
from app.services.pdf_detailed_service import generate_pdf_detailed
from app.services.video_service import analyze_video, extract_run_date
from app.services.video_quality import analyze_video_quality
from app.utils.formatter import format_analysis
from app.utils.video_parser import parse_video_analysis
from app.utils.logger import logger, log_event
from app.repositories import (
    save_user,
    save_analysis,
    save_feedback,
    update_athlete_profile,
    get_user_profile,
    track,
    get_stats_window,
    get_funnel,
    get_retention_metrics,
    get_top_users,
    get_user_timeline,
    get_recent_errors,
    is_approved,
    request_access,
    approve_user,
    deny_user,
    list_pending,
)
import app.state as state

ALLOWED_USERS = [202921941, 201955370]
OWNER_ID = int(os.getenv("OWNER_ID", str(ALLOWED_USERS[0])))  # admin receives reports + alerts
from datetime import datetime, time as _dtime, timedelta
try:
    from zoneinfo import ZoneInfo
    _MSK = ZoneInfo("Europe/Moscow")
except Exception:
    _MSK = None

# Per-user video-analysis lock: prevents same user from starting 2 parallel runs
_video_locks: dict[int, asyncio.Lock] = {}
# Global semaphore caps concurrent video analyses (prevents OOM / thread exhaustion)
_video_sem = asyncio.Semaphore(2)

def _get_video_lock(user_id: int) -> asyncio.Lock:
    lock = _video_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _video_locks[user_id] = lock
    return lock


# ── OWNER NOTIFICATIONS / STATS ────────────────────────────────────────────────

async def notify_owner(context, text: str):
    """Send alert/report to bot owner. Silent on failure."""
    try:
        await context.bot.send_message(OWNER_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"notify_owner failed: {e}")


def _fmt_stats_report(stats: dict, funnel: dict, title: str) -> str:
    """Format stats dict as a Telegram-friendly report."""
    days = stats["window_days"]
    lines = [f"<b>{title}</b>\n"]

    lines.append(f"📊 <b>Period</b>: {days} day(s)")
    lines.append(f"👥 New users: {stats['new_users']}")
    lines.append(f"🟢 Active users: {stats['active_users']}")

    a = stats["analyses"]
    total_ok = a.get("success", 0)
    total_err = a.get("error", 0)
    lines.append(f"")
    lines.append(f"🎿 <b>Analyses</b>: {total_ok + total_err}")
    lines.append(f"  ✓ success: {total_ok}")
    lines.append(f"  ✗ error: {total_err}")

    if stats["by_mode"]:
        parts = " ".join(f"{k}={v}" for k, v in stats["by_mode"].items())
        lines.append(f"  mode: {parts}")
    if stats["by_discipline"]:
        parts = " ".join(f"{k}={v}" for k, v in stats["by_discipline"].items())
        lines.append(f"  discipline: {parts}")
    if stats["by_lang"]:
        parts = " ".join(f"{k}={v}" for k, v in stats["by_lang"].items())
        lines.append(f"  lang: {parts}")

    fb = stats["feedback"]
    if fb:
        lines.append(f"")
        lines.append(f"💬 <b>Feedback</b>")
        lines.append(f"  👍 {fb.get('positive', 0)}")
        lines.append(f"  👎 {fb.get('negative', 0)}")

    if funnel:
        lines.append(f"")
        lines.append(f"⏱ <b>Funnel</b> (unique users who reached stage)")
        for stage, cnt in funnel.items():
            if cnt > 0:
                lines.append(f"  {stage}: {cnt}")

    # OpenAI cost breakdown
    ob = stats.get("openai_breakdown") or []
    total_cost = stats.get("openai_cost_total", 0) or 0
    if ob or total_cost:
        lines.append(f"")
        lines.append(f"💰 <b>OpenAI</b>: ")
        for row in ob:
            m = row.get("model") or "?"
            calls = row.get("calls") or 0
            pt = row.get("prompt_tokens") or 0
            ct = row.get("completion_tokens") or 0
            cost = row.get("cost_usd") or 0
            lat = row.get("avg_latency") or 0
            lines.append(f"  {m}: {calls} calls, {pt + ct} tok, , ~{lat:.1f}s")

    errs = stats.get("recent_errors") or []
    if errs:
        lines.append(f"")
        lines.append(f"⚠️ <b>Recent errors</b> ({len(errs)})")
        for e in errs[:5]:
            t = e["created_at"].strftime("%m-%d %H:%M") if hasattr(e["created_at"], "strftime") else str(e["created_at"])
            place = e.get("place") or "?"
            msg = (e.get("msg") or "")[:120]
            lines.append(f"  [{t}] {place}: {msg}")

    return "\n".join(lines)




# ── ACCESS CONTROL ─────────────────────────────────────────────────────────────

async def require_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user can use bot. If not, record request + notify owner once.
    Returns True if user is approved and caller should proceed."""
    user = update.effective_user
    if user is None:
        return False
    if is_approved(user.id):
        return True
    # Not approved — handle request
    result = request_access(user.id, username=user.username, first_name=user.first_name)
    # Pick user lang for reply
    lang_code = (user.language_code or "")
    lang = "ru" if lang_code.startswith("ru") else "en"
    # Already denied — silent ignore
    if result["status"] == "denied":
        return False
    # Reply with pending status
    msg_ru = "⏳ Бот в закрытой beta. Запрос на доступ отправлен — пришлю уведомление когда откроют."
    msg_en = "⏳ Bot is in closed beta. Your access request has been submitted — you'\''ll be notified when approved."
    text = msg_ru if lang == "ru" else msg_en
    try:
        if update.message:
            await update.message.reply_text(text)
        elif update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
    except Exception:
        pass
    # Notify owner only on new (first-time) request
    if result["is_new_request"]:
        track(user.id, "access_requested", username=user.username, first_name=user.first_name)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Allow", callback_data=f"allow_{user.id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny_{user.id}"),
        ]])
        info = (
            f"⚠️ <b>New access request</b>\n"
            f"id: <code>{user.id}</code>\n"
            f"username: @{user.username or '-'}\n"
            f"name: {user.first_name or '-'}\n"
            f"lang: {lang_code or '-'}"
        )
        try:
            await context.bot.send_message(OWNER_ID, info, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.warning(f"notify owner (access request) failed: {e}")
    return False




# ── ADMIN PANEL HELPERS (reusable builders) ────────────────────────────────────

def _build_stats_report(days: int, title: str) -> str:
    stats = get_stats_window(days=days)
    funnel = get_funnel(days=days)
    return _fmt_stats_report(stats, funnel, title)


def _build_retention_report() -> str:
    m = get_retention_metrics()
    top = get_top_users(days=7, limit=5)
    lines = ["<b>📈 Retention</b>\n"]
    lines.append(f"DAU (24h): {m['DAU']}")
    lines.append(f"WAU (7d): {m['WAU']}")
    lines.append(f"MAU (30d): {m['MAU']}")
    lines.append(f"Returning (7d): {m['returning_7d']}")
    if top:
        lines.append("")
        lines.append("<b>🏆 Top users (7d)</b>")
        for r in top:
            uid = r.get("telegram_user_id")
            name = r.get("athlete_name") or r.get("username") or f"user_{uid}"
            events = r.get("events") or 0
            analyses = r.get("analyses") or 0
            lines.append(f"  {name}: {events} events, {analyses} analyses")
    return "\n".join(lines)


def _build_errors_report() -> str:
    errs = get_recent_errors(limit=10)
    if not errs:
        return "No recent errors 🟢"
    lines = ["<b>⚠️ Last 10 errors</b>\n"]
    for e in errs:
        t_ = e["created_at"].strftime("%m-%d %H:%M") if hasattr(e["created_at"], "strftime") else str(e["created_at"])
        place = e.get("place") or "?"
        msg = (e.get("msg") or "")[:120]
        uid = e.get("telegram_user_id") or "-"
        lines.append(f"[{t_}] user={uid} <b>{place}</b>: <code>{msg}</code>")
    return "\n".join(lines)


def _build_pending_report() -> str:
    rows = list_pending()
    if not rows:
        return "No pending requests 🟢"
    lines = [f"<b>⏳ Pending requests ({len(rows)})</b>\n"]
    for r in rows:
        t_ = r["requested_at"].strftime("%m-%d %H:%M") if hasattr(r["requested_at"], "strftime") else str(r["requested_at"])
        uname = f"@{r['username']}" if r.get("username") else ""
        fname = r.get("first_name") or ""
        lines.append(f"[{t_}] <code>{r['telegram_user_id']}</code> {uname} {fname}".strip())
        lines.append(f"  /allow {r['telegram_user_id']}  |  /deny {r['telegram_user_id']}")
    return "\n".join(lines)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats [today|week|month] — send stats report to requester."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    args = context.args or []
    arg = args[0].lower() if args else "today"
    if arg == "week":
        report = _build_stats_report(7, "📊 Stats · last 7 days")
    elif arg == "month":
        report = _build_stats_report(30, "📊 Stats · last 30 days")
    else:
        report = _build_stats_report(1, "📊 Stats · last 24h")
    await update.message.reply_text(report, parse_mode="HTML")


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled daily report — sent to OWNER at 09:00 MSK."""
    try:
        stats = get_stats_window(days=1)
        funnel = get_funnel(days=1)
        title = f"🌅 Daily report · {datetime.now(_MSK).strftime('%d.%m.%Y') if _MSK else datetime.now().strftime('%d.%m.%Y')}"
        report = _fmt_stats_report(stats, funnel, title)
        await context.bot.send_message(OWNER_ID, report, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"daily_report_job failed: {e}")


async def errors_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/errors — last 10 errors across all users."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    errs = get_recent_errors(limit=10)
    if not errs:
        await update.message.reply_text("No recent errors 🟢")
        return
    lines = ["<b>⚠️ Last 10 errors</b>\n"]
    for e in errs:
        t = e["created_at"].strftime("%m-%d %H:%M") if hasattr(e["created_at"], "strftime") else str(e["created_at"])
        place = e.get("place") or "?"
        msg = (e.get("msg") or "")[:120]
        uid = e.get("telegram_user_id") or "-"
        lines.append(f"[{t}] user={uid} <b>{place}</b>: <code>{msg}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/user <telegram_id> — show recent events for a user."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /user <telegram_id>")
        return
    try:
        target = int(args[0])
    except ValueError:
        await update.message.reply_text("user_id must be integer")
        return
    profile = get_user_profile(target)
    timeline = get_user_timeline(target, limit=20)
    lines = [f"<b>👤 User {target}</b>\n"]
    if profile:
        lines.append(f"Athlete: {profile.get('athlete_name') or '-'}")
        lines.append(f"Birth year: {profile.get('birth_year') or '-'}")
        lines.append(f"Category: {profile.get('category') or '-'}")
    else:
        lines.append("<i>No profile record</i>")
    lines.append("")
    if not timeline:
        lines.append("<i>No events</i>")
    else:
        lines.append(f"<b>Last {len(timeline)} events</b>:")
        for e in timeline:
            t = e["created_at"].strftime("%m-%d %H:%M") if hasattr(e["created_at"], "strftime") else str(e["created_at"])
            et = e["event_type"]
            payload = e.get("payload") or {}
            # Shorten payload for display
            if isinstance(payload, dict):
                p_str = " ".join(f"{k}={v}" for k, v in payload.items() if k not in ("message",))[:80]
            else:
                p_str = str(payload)[:80]
            lines.append(f"[{t}] {et} · {p_str}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def retention_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/retention — DAU / WAU / MAU + returning users."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    m = get_retention_metrics()
    top = get_top_users(days=7, limit=5)
    lines = ["<b>📈 Retention</b>\n"]
    lines.append(f"DAU (24h): {m['DAU']}")
    lines.append(f"WAU (7d): {m['WAU']}")
    lines.append(f"MAU (30d): {m['MAU']}")
    lines.append(f"Returning (active in last 7d + prior 7d): {m['returning_7d']}")
    if top:
        lines.append("")
        lines.append("<b>🏆 Top users (7d)</b>")
        for r in top:
            uid = r.get("telegram_user_id")
            name = r.get("athlete_name") or r.get("username") or f"user_{uid}"
            events = r.get("events") or 0
            analyses = r.get("analyses") or 0
            lines.append(f"  {name}: {events} events, {analyses} analyses")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")




async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/allow <telegram_id> — grant access."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /allow <telegram_id>")
        return
    try:
        target = int(args[0])
    except ValueError:
        await update.message.reply_text("user_id must be integer")
        return
    changed = approve_user(target, approved_by=user_id)
    # Notify target
    if changed:
        try:
            await context.bot.send_message(
                target,
                "✓ Доступ открыт! Нажми /start чтобы начать.\n✓ Access granted! Tap /start to begin."
            )
        except Exception as e:
            logger.warning(f"welcome message to {target} failed: {e}")
    track(user_id, "user_approved", target=target, changed=changed)
    await update.message.reply_text(f"{'✓ Approved' if changed else 'Already approved'}: {target}")


async def deny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deny <telegram_id> — deny access."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /deny <telegram_id>")
        return
    try:
        target = int(args[0])
    except ValueError:
        await update.message.reply_text("user_id must be integer")
        return
    changed = deny_user(target, denied_by=user_id)
    track(user_id, "user_denied", target=target, changed=changed)
    await update.message.reply_text(f"{'✗ Denied' if changed else 'Already denied'}: {target}")


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pending — list users awaiting approval."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    rows = list_pending()
    if not rows:
        await update.message.reply_text("No pending requests 🟢")
        return
    lines = [f"<b>⏳ Pending requests ({len(rows)})</b>\n"]
    for r in rows:
        t_str = r["requested_at"].strftime("%m-%d %H:%M") if hasattr(r["requested_at"], "strftime") else str(r["requested_at"])
        uname = f"@{r['username']}" if r.get("username") else ""
        fname = r.get("first_name") or ""
        lines.append(f"[{t_str}] <code>{r['telegram_user_id']}</code> {uname} {fname}".strip())
        lines.append(f"  /allow {r['telegram_user_id']}  |  /deny {r['telegram_user_id']}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")




async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin — open admin panel (owner only)."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
        return
    lang = state.get_lang(user_id)
    await update.message.reply_text(
        t(lang, "admin_title"),
        parse_mode="HTML",
        reply_markup=keyboard_admin_panel(lang),
    )

async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled weekly report — Mondays 09:00 MSK."""
    try:
        stats = get_stats_window(days=7)
        funnel = get_funnel(days=7)
        ret = get_retention_metrics()
        top = get_top_users(days=7, limit=5)
        title = f"📅 Weekly report · {datetime.now(_MSK).strftime('%d.%m.%Y') if _MSK else datetime.now().strftime('%d.%m.%Y')}"
        report = _fmt_stats_report(stats, funnel, title)
        # Append retention + top users
        extra = ["", "<b>📈 Retention</b>",
                 f"  DAU: {ret['DAU']}, WAU: {ret['WAU']}, MAU: {ret['MAU']}",
                 f"  Returning (7d): {ret['returning_7d']}"]
        if top:
            extra.append("")
            extra.append("<b>🏆 Top users</b>")
            for r in top:
                uid = r.get("telegram_user_id")
                name = r.get("athlete_name") or r.get("username") or f"user_{uid}"
                lines = f"  {name}: {r.get('events')} events, {r.get('analyses')} analyses"
                extra.append(lines)
        report += "\n" + "\n".join(extra)
        await context.bot.send_message(OWNER_ID, report, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"weekly_report_job failed: {e}")






# ── ЯЗЫК ───────────────────────────────────────────────────────────────────────

def detect_lang(user) -> str:
    lang_code = getattr(user, "language_code", None) or ""
    return "ru" if lang_code.startswith("ru") else "en"


# ── ТЕКСТЫ ─────────────────────────────────────────────────────────────────────

T = {
    "ru": {
        "welcome":          "🎿 <b>Alpine Ski Performance Lab</b> — AI-тренер по горнолыжной технике.\n\n📸 По фото — быстрый снимок техники\n🎥 По видео — детальный разбор заезда\n\nОтчёт за 2 минуты. Выбери тип 👇",
        "start_btn":        "🎿 Начать анализ",
        "admin_btn":        "⚙️ Админ",
        "admin_title":      "⚙️ <b>Админ-панель</b>",
        "adm_stats_1d":     "📊 Stats · 24ч",
        "adm_stats_7d":     "📅 Stats · 7 дней",
        "adm_stats_30d":    "📆 Stats · 30 дней",
        "adm_retention":    "📈 Retention",
        "adm_errors":       "⚠️ Errors",
        "adm_pending":      "⏳ Pending",
        "adm_back":         "← Назад",
        "adm_close":        "✖ Закрыть",
        "no_access":        "Недостаточно прав",
        "choose_mode":      "Выберите тип анализа 👇",
        "btn_quick":        "⚡ Быстрый анализ",
        "btn_detailed":     "🔍 Детальный анализ",
        "ask_name":         "Как зовут спортсмена?",
        "ask_year":         "Введите год рождения (например 2015)",
        "bad_year":         "Введите корректный год (например 2015)",
        "bad_year2":        "Проверьте год рождения - выглядит некорректно",
        "choose_disc":      "Категория: {cat}\nВыберите дисциплину",
        "disc_chosen":      "Выбрана дисциплина: {disc}\n\nОтправь 3-5 фото с одного заезда - разные фазы поворота: вход, середина дуги, выход.\nАлгоритм автоматически отберёт наиболее информативные.",
        "ask_run_type":     "Тип заезда:",
        "btn_training":     "🏋️ Тренировка",
        "btn_race":         "🏁 Гонка",
        "ask_video":        "Отправь видео заезда (до 60 сек)\nЛучше всего: съёмка сбоку трассы",
        "video_saved":            "Видео получено ✓",
        "date_saved":             "📅 Дата заезда: {date}",
        "date_saved_auto":        "📅 Дата заезда: {date} (из видео)",
        "date_saved_default":     "📅 Дата заезда: {date} (по умолчанию — измените если нужно)",
        "change_date_btn":        "📅 Изменить дату",
        "ask_date":               "Когда был этот заезд?",
        "date_today":             "Сегодня",
        "date_yesterday":         "Вчера",
        "date_3d":                "3 дня назад",
        "date_1w":                "Неделю назад",
        "date_2w":                "2 недели назад",
        "date_1m":                "Месяц назад",
        "date_calendar":          "📅 Выбрать в календаре",
        "cal_title":              "Выберите дату заезда",
        "cal_back_presets":       "← К пресетам",
        "month_ru_1":  "Январь",   "month_ru_2":  "Февраль", "month_ru_3":  "Март",
        "month_ru_4":  "Апрель",   "month_ru_5":  "Май",     "month_ru_6":  "Июнь",
        "month_ru_7":  "Июль",     "month_ru_8":  "Август",  "month_ru_9":  "Сентябрь",
        "month_ru_10": "Октябрь",  "month_ru_11": "Ноябрь",  "month_ru_12": "Декабрь",
        "video_too_long":         "Видео слишком длинное. Максимум 60 секунд.",
        "video_error":            "⚠️ Не удалось проанализировать видео. Возможные причины: слишком короткое/длинное видео, плохое освещение или проблема на нашей стороне. Администратор уведомлён. /start — попробовать снова",
        "ask_before_analyze":     "Есть фото с этого заезда? Добавь для точности — или сразу анализировать.",
        "btn_add_photos":         "📸 Добавить фото",
        "btn_analyze_now":        "🔍 Анализировать",
        "send_photos_prompt":     "Отправь фото (до 5 штук). После каждого фото можешь сразу анализировать.",
        "send_photos_hint":       "Отправь фото (до 5 штук) или сразу анализируй видео 👇",
        "extra_photo_added":      "Фото {n}/5 получено",
        "wait_more_photos":       "Жду ещё фото 👇",
        "analyzing_video":        "Анализирую... займёт 1-2 минуты",
        "analysis_complete":      "Анализ завершён ✓",
        "photo_count":      "Фото получено ({n}/5) - нужно минимум 3",
        "photo_3":          "Фото получено ({n}/5) ✓\n\nМожешь добавить ещё 1-2 для точности или сразу начать анализ.",
        "photo_4":          "Фото получено ({n}/5)\n\nМожешь добавить ещё одно или начать анализ.",
        "photo_5":          "Фото получено (5/5) - отличный набор 👍",
        "photo_max":        "Максимум 5 фото. Нажми «Анализировать» 👇",
        "add_more":         "Жду ещё фото. Уже загружено: {n}/5",
        "need_3":           "Нужно минимум 3 фото",
        "checking":         "Оцениваю качество и состав фото...",
        "rejected":         "Фото не подходят для анализа.\n{issues}\n\nПопробуй загрузить другие - начни с /start",
        "need_more_ph":     "Подходящих фото: {good} из {total} - маловато для надёжного анализа.\n{missing}",
        "can_add_more":     "Можешь добавить ещё {n} фото и повторить анализ.",
        "can_use_approved": "У тебя есть {n} подходящих фото. Можешь анализировать или добавить новые.",
        "missing_hdr":      "Чего не хватает:\n",
        "warning_ok":       "Фото приняты, но кое-чего не хватает:\n{missing}\n\nМожешь добавить ещё {left} фото или анализировать что есть.",
        "warning_limit":    "Фото приняты, но кое-чего не хватает:\n{missing}\n\nДостигнут лимит 5 фото. Можешь заменить неподходящие или анализировать что есть.",
        "analyzing":        "Анализирую технику...",
        "restart":          "Начинаем заново. Отправь 3-5 фото с одного заезда - разные фазы поворота: вход, середина дуги, выход.",
        "rate":             "Оцените анализ 👇",
        "thanks_good":      "Спасибо 🙌",
        "ask_bad":          "Что было не так?",
        "thanks_fb":        "Спасибо за обратную связь!",
        "error":            "⚠️ Что-то пошло не так при анализе. Администратор уже уведомлён, мы разбираемся. Попробуйте позже — /start",
        "btn_analyze":      "🔍 Анализировать",
        "btn_add":          "📸 Добавить фото",
        "btn_restart":      "🔄 Начать заново",
        "btn_sl":           "SL",
        "btn_gs":           "GS",
        "test_mode":        "Бот в тестовом режиме",
        "btn_new_video":    "🎬 Другое видео",
        "new_video_prompt": "Отправь новое видео (до 60 сек)",
    },
    "en": {
        "welcome":          "Hi! I analyze alpine skiing technique from photos and video.\n\nChoose analysis type 👇",
        "start_btn":        "🎿 Start analysis",
        "admin_btn":        "⚙️ Admin",
        "admin_title":      "⚙️ <b>Admin panel</b>",
        "adm_stats_1d":     "📊 Stats · 24h",
        "adm_stats_7d":     "📅 Stats · 7d",
        "adm_stats_30d":    "📆 Stats · 30d",
        "adm_retention":    "📈 Retention",
        "adm_errors":       "⚠️ Errors",
        "adm_pending":      "⏳ Pending",
        "adm_back":         "← Back",
        "adm_close":        "✖ Close",
        "no_access":        "Not authorized",
        "choose_mode":      "Choose analysis type 👇",
        "btn_quick":        "⚡ Quick analysis",
        "btn_detailed":     "🔍 Detailed analysis",
        "ask_name":         "What is the athlete's name?",
        "ask_year":         "Enter year of birth (e.g. 2015)",
        "bad_year":         "Please enter a valid year (e.g. 2015)",
        "bad_year2":        "Please check the year of birth - looks incorrect",
        "choose_disc":      "Category: {cat}\nChoose discipline",
        "disc_chosen":      "Discipline selected: {disc}\n\nSend 3-5 photos from one run - different phases: entry, mid-arc, exit.\nThe algorithm will automatically select the most informative ones.",
        "ask_run_type":     "Run type:",
        "btn_training":     "🏋️ Training",
        "btn_race":         "🏁 Race",
        "ask_video":        "Send your run video (max 60 sec)\nBest: filmed from the side of the course",
        "video_saved":            "Video received ✓",
        "date_saved":             "📅 Run date: {date}",
        "date_saved_auto":        "📅 Run date: {date} (from video)",
        "date_saved_default":     "📅 Run date: {date} (default — change if needed)",
        "change_date_btn":        "📅 Change date",
        "ask_date":               "When was this run?",
        "date_today":             "Today",
        "date_yesterday":         "Yesterday",
        "date_3d":                "3 days ago",
        "date_1w":                "1 week ago",
        "date_2w":                "2 weeks ago",
        "date_1m":                "1 month ago",
        "date_calendar":          "📅 Pick in calendar",
        "cal_title":              "Choose run date",
        "cal_back_presets":       "← Back to presets",
        "month_en_1":  "January",   "month_en_2":  "February", "month_en_3":  "March",
        "month_en_4":  "April",     "month_en_5":  "May",      "month_en_6":  "June",
        "month_en_7":  "July",      "month_en_8":  "August",   "month_en_9":  "September",
        "month_en_10": "October",   "month_en_11": "November", "month_en_12": "December",
        "video_too_long":         "Video too long. Max 60 seconds.",
        "video_error":            "⚠️ Could not analyse the video. Possible reasons: too short/long, poor lighting, or our-side issue. Admin notified. Tap /start to retry",
        "ask_before_analyze":     "Have photos from this run? Add them for better accuracy — or analyse now.",
        "btn_add_photos":         "📸 Add photos",
        "btn_analyze_now":        "🔍 Analyse now",
        "send_photos_prompt":     "Send photos (up to 5). After each photo you can analyse right away.",
        "send_photos_hint":       "Send photos (up to 5) or analyse video now 👇",
        "extra_photo_added":      "Photo {n}/5 received",
        "wait_more_photos":       "Send more photos 👇",
        "analyzing_video":        "Analysing... 1-2 min",
        "analysis_complete":      "Analysis complete ✓",
        "photo_count":      "Photo received ({n}/5) - need at least 3",
        "photo_3":          "Photo received ({n}/5) ✓\n\nYou can add 1-2 more for accuracy or start analysis now.",
        "photo_4":          "Photo received ({n}/5)\n\nYou can add one more or start analysis.",
        "photo_5":          "Photo received (5/5) - great set 👍",
        "photo_max":        "Maximum 5 photos. Tap Analyse 👇",
        "add_more":         "Waiting for more photos. Uploaded: {n}/5",
        "need_3":           "Need at least 3 photos",
        "checking":         "Reviewing photo quality and coverage...",
        "rejected":         "Photos are not suitable for analysis.\n{issues}\n\nTry uploading different ones - start with /start",
        "need_more_ph":     "Suitable photos: {good} of {total} - not enough for reliable analysis.\n{missing}",
        "can_add_more":     "You can add {n} more photos and try again.",
        "can_use_approved": "You have {n} approved photos. You can analyse them or add new ones.",
        "missing_hdr":      "What's missing:\n",
        "warning_ok":       "Photos accepted, but something is missing:\n{missing}\n\nYou can add {left} more photos or analyse as is.",
        "warning_limit":    "Photos accepted, but something is missing:\n{missing}\n\nLimit of 5 photos reached. You can replace unsuitable ones or analyse as is.",
        "analyzing":        "Analysing technique...",
        "restart":          "Starting over. Send 3-5 photos from one run - different phases: entry, mid-arc, exit.",
        "rate":             "Rate the analysis 👇",
        "thanks_good":      "Thank you 🙌",
        "ask_bad":          "What went wrong?",
        "thanks_fb":        "Thank you for the feedback!",
        "error":            "⚠️ Something went wrong during analysis. The admin has been notified. Please try again later — /start",
        "btn_analyze":      "🔍 Analyse",
        "btn_add":          "📸 Add photo",
        "btn_restart":      "🔄 Start over",
        "btn_sl":           "SL",
        "btn_gs":           "GS",
        "test_mode":        "Bot is in test mode",
        "btn_new_video":    "🎬 New video",
        "new_video_prompt": "Send new video (max 60 sec)",
    }
}

def t(lang: str, key: str, **kwargs) -> str:
    text = T.get(lang, T["en"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


# ── KEYBOARDS ──────────────────────────────────────────────────────────────────

def keyboard_start(lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "start_btn"), callback_data="start_flow")
    ]])


def keyboard_admin_panel(lang):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "adm_stats_1d"), callback_data="admin_stats_1"),
            InlineKeyboardButton(t(lang, "adm_stats_7d"), callback_data="admin_stats_7"),
        ],
        [
            InlineKeyboardButton(t(lang, "adm_stats_30d"), callback_data="admin_stats_30"),
            InlineKeyboardButton(t(lang, "adm_retention"), callback_data="admin_retention"),
        ],
        [
            InlineKeyboardButton(t(lang, "adm_errors"), callback_data="admin_errors"),
            InlineKeyboardButton(t(lang, "adm_pending"), callback_data="admin_pending"),
        ],
        [InlineKeyboardButton(t(lang, "adm_close"), callback_data="admin_back")],
    ])

def keyboard_analyze(lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "btn_analyze"), callback_data="do_analyze")
    ]])

def keyboard_add_or_analyze(lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "btn_analyze"), callback_data="do_analyze"),
        InlineKeyboardButton(t(lang, "btn_add"), callback_data="add_more"),
    ]])

def keyboard_warning(lang, at_limit=False):
    if at_limit:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "btn_restart"), callback_data="restart"),
            InlineKeyboardButton(t(lang, "btn_analyze"), callback_data="do_analyze_confirmed"),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "btn_analyze"), callback_data="do_analyze_confirmed"),
        InlineKeyboardButton(t(lang, "btn_add"), callback_data="add_more"),
    ]])

def keyboard_mode(lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "btn_quick"),    callback_data="mode_quick"),
        InlineKeyboardButton(t(lang, "btn_detailed"), callback_data="mode_detailed"),
    ]])

def keyboard_run_type(lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "btn_training"), callback_data="run_type_training"),
        InlineKeyboardButton(t(lang, "btn_race"),     callback_data="run_type_race"),
    ]])

def keyboard_before_analyze(lang):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "btn_analyze_now"), callback_data="analyze_now"),
        ],
        [
            InlineKeyboardButton(t(lang, "btn_add_photos"),  callback_data="add_photos_before"),
            InlineKeyboardButton(t(lang, "btn_new_video"), callback_data="upload_new_video"),
        ],
    ])

def keyboard_change_date(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "change_date_btn"), callback_data="date_change")],
    ])



def _month_name(month: int, lang: str) -> str:
    return t(lang, f"month_{'ru' if lang == 'ru' else 'en'}_{month}")


def keyboard_calendar(year: int, month: int, lang: str):
    """Build a month-grid calendar inline keyboard.
    - Navigation row: ‹ Month Year ›
    - 7 columns (Mo..Su), day buttons with callback date_pick_YYYY-MM-DD
    - Back button returns to preset list
    """
    from calendar import monthrange
    # Clamp month/year
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1

    prev_month = month - 1 if month > 1 else 12
    prev_year  = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year  = year if month < 12 else year + 1

    rows = []
    # Navigation row
    rows.append([
        InlineKeyboardButton("‹", callback_data=f"cal_nav_{prev_year}_{prev_month}"),
        InlineKeyboardButton(f"{_month_name(month, lang)} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton("›", callback_data=f"cal_nav_{next_year}_{next_month}"),
    ])
    # Day-of-week headers
    if lang == "ru":
        dows = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    else:
        dows = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    rows.append([InlineKeyboardButton(d, callback_data="cal_ignore") for d in dows])

    # Day grid
    first_weekday, days_in_month = monthrange(year, month)
    # Python monthrange: first_weekday 0=Monday
    current_row = []
    # Leading blanks
    for _ in range(first_weekday):
        current_row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
    for day in range(1, days_in_month + 1):
        current_row.append(InlineKeyboardButton(
            str(day),
            callback_data=f"date_pick_{year:04d}-{month:02d}-{day:02d}"
        ))
        if len(current_row) == 7:
            rows.append(current_row)
            current_row = []
    # Trailing blanks
    while current_row and len(current_row) < 7:
        current_row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
    if current_row:
        rows.append(current_row)

    # Back to presets
    rows.append([InlineKeyboardButton(t(lang, "cal_back_presets"), callback_data="date_change")])
    return InlineKeyboardMarkup(rows)


def keyboard_date_presets(lang):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "date_today"), callback_data="date_set_0"),
            InlineKeyboardButton(t(lang, "date_yesterday"), callback_data="date_set_1"),
        ],
        [
            InlineKeyboardButton(t(lang, "date_3d"), callback_data="date_set_3"),
            InlineKeyboardButton(t(lang, "date_1w"), callback_data="date_set_7"),
        ],
        [
            InlineKeyboardButton(t(lang, "date_2w"), callback_data="date_set_14"),
            InlineKeyboardButton(t(lang, "date_1m"), callback_data="date_set_30"),
        ],
        [InlineKeyboardButton(t(lang, "date_calendar"), callback_data="cal_open")],
    ])


def keyboard_extra_photos(lang):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "btn_analyze_now"),                callback_data="analyze_now"),
        InlineKeyboardButton("📸 Ещё" if lang == "ru" else "📸 More", callback_data="more_extra"),
    ]])


# ── CATEGORY ───────────────────────────────────────────────────────────────────

def get_category_from_birth_year(birth_year: int) -> str:
    age = datetime.now().year - birth_year
    if age <= 8:    return "U8"
    elif age <= 10: return "U10"
    elif age <= 12: return "U12"
    elif age <= 14: return "U14"
    elif age <= 16: return "U16"
    elif age <= 18: return "U18"
    else:           return "Adult"


# ── QUALITY CHECK PARSER ───────────────────────────────────────────────────────

def parse_quality_result(quality_text: str) -> dict:
    result = {
        "status": "OK", "good_photos": 0,
        "bad_indexes": [],
        "issues": [], "missing": [],
        "discipline_match": "OK", "age_match": "OK"
    }
    section = None
    empty_values = {"нет", "none", "no", "-", "n/a", "нет замечаний", "no issues"}
    for line in quality_text.splitlines():
        line = line.strip()
        if line.startswith("STATUS:"):
            result["status"] = line.split(":", 1)[1].strip()
        elif line.startswith("GOOD_PHOTOS:"):
            try: result["good_photos"] = int(re.search(r"\d+", line).group())
            except: pass
        elif line.startswith("BAD_INDEXES:"):
            val = line.split(":", 1)[1].strip()
            if val.upper() != "NONE" and val != "-":
                try:
                    result["bad_indexes"] = [int(x.strip()) for x in val.split(",") if x.strip().isdigit()]
                except: pass
        elif line.startswith("DISCIPLINE_MATCH:"):
            result["discipline_match"] = line.split(":", 1)[1].strip()
        elif line.startswith("AGE_MATCH:"):
            result["age_match"] = line.split(":", 1)[1].strip()
        elif line.startswith("ISSUES:"):
            section = "issues"
        elif line.startswith("MISSING:"):
            section = "missing"
        elif line.startswith("- "):
            val = line[2:].strip()
            if val.lower() in empty_values:
                continue
            if section == "issues": result["issues"].append(val)
            elif section == "missing": result["missing"].append(val)
    return result


# ── HANDLERS ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not await require_access(update, context):
        return

    lang = detect_lang(user)
    state.set_lang(user.id, lang)
    state.reset_session(user.id)

    log_event("user_started", user_id=user.id, lang=lang)
    track(user.id, "start", lang=lang, username=user.username)
    # Identify user in PostHog (traits update on every /start)
    try:
        from app.utils.posthog_client import identify as _ph_identify
        _profile = get_user_profile(user.id) or {}
        _ph_identify(user.id, {
            "username": user.username,
            "first_name": user.first_name,
            "language_code": getattr(user, "language_code", None),
            "lang": lang,
            "athlete_name": _profile.get("athlete_name"),
            "birth_year": _profile.get("birth_year"),
            "category": _profile.get("category"),
        })
    except Exception:
        pass
    save_user(telegram_user_id=user.id, username=user.username, first_name=user.first_name)

    await update.message.reply_text(
        t(lang, "welcome"),
        reply_markup=keyboard_mode(lang),
        parse_mode="HTML"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    if not await require_access(update, context):
        return

    lang = state.get_lang(user_id)
    text = update.message.text.strip()
    cur_state = state.get_state(user_id)

    if cur_state == "waiting_name":
        update_athlete_profile(user_id, name=text)
        state.set_state(user_id, "waiting_birth_year")
        await update.message.reply_text(t(lang, "ask_year"))

    elif cur_state == "waiting_birth_year":
        try:
            birth_year = int(text)
            current_year = datetime.now().year
            if birth_year < 2000 or birth_year > current_year:
                await update.message.reply_text(t(lang, "bad_year"))
                return
            age = current_year - birth_year
            if age < 5 or age > 40:
                await update.message.reply_text(t(lang, "bad_year2"))
                return
            category = get_category_from_birth_year(birth_year)
            update_athlete_profile(user_id, birth_year=birth_year, category=category)
            state.set_category(user_id, category)
            state.set_state(user_id, "waiting_discipline")
            await update.message.reply_text(
                t(lang, "choose_disc", cat=category),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(lang, "btn_sl"), callback_data="disc_sl"),
                    InlineKeyboardButton(t(lang, "btn_gs"), callback_data="disc_gs"),
                ]])
            )
        except Exception as e:
            logger.exception(f"birth_year_error {e}")
            await update.message.reply_text(t(lang, "bad_year"))

    elif cur_state == "waiting_feedback":
        analysis_id = state.get_last_analysis_id(user_id)
        save_feedback(user_id, analysis_id, "negative", text)
        state.set_state(user_id, None)
        await update.message.reply_text(t(lang, "thanks_fb"))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not await require_access(update, context):
        return

    lang = state.get_lang(user_id)
    cur_state = state.get_state(user_id)

    # detailed mode: collecting optional photos before video analysis
    if cur_state == "waiting_extra_photos":
        file_id = update.message.photo[-1].file_id
        state.append_photo(user_id, file_id)
        n = len(state.get_photos(user_id))
        if n < 5:
            sent = await update.message.reply_text(
                t(lang, "extra_photo_added", n=n),
                reply_markup=keyboard_extra_photos(lang)
            )
        else:
            sent = await update.message.reply_text(
                f"📸 5/5 ✓",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(lang, "btn_analyze_now"), callback_data="analyze_now"),
                ]])
            )
        # Track counter messages so they can be cleaned up when analysis starts
        msg_ids = state.get(user_id, "photo_msg_ids", []) or []
        msg_ids.append(sent.message_id)
        state.set(user_id, "photo_msg_ids", msg_ids)
        return

    approved = state.get_approved(user_id)
    limit = state.get_photo_limit(user_id)
    new_photos = state.get_photos(user_id)

    if len(new_photos) >= limit:
        await update.message.reply_text(t(lang, "photo_max"), reply_markup=keyboard_analyze(lang))
        return

    file_id = update.message.photo[-1].file_id
    state.append_photo(user_id, file_id)
    new_photos = state.get_photos(user_id)

    new_count = len(new_photos)
    total = len(approved) + new_count

    # Show total (approved + newly uploaded) consistently
    if total < 3:
        sent = await update.message.reply_text(t(lang, "photo_count", n=total))
    elif total == 3:
        sent = await update.message.reply_text(t(lang, "photo_3", n=total), reply_markup=keyboard_add_or_analyze(lang))
    elif total < 5:
        sent = await update.message.reply_text(t(lang, "photo_4", n=total), reply_markup=keyboard_add_or_analyze(lang))
    else:
        sent = await update.message.reply_text(t(lang, "photo_5"), reply_markup=keyboard_analyze(lang))

    # Track message ID for later cleanup on analysis start
    msg_ids = state.get(user_id, "photo_msg_ids", []) or []
    msg_ids.append(sent.message_id)
    state.set(user_id, "photo_msg_ids", msg_ids)
    track(user_id, "photo_uploaded", total=total, approved=len(approved), new=new_count)


async def get_file_url(bot, file_id):
    file = await bot.get_file(file_id)
    return file.file_path


# ── CORE ANALYSIS ──────────────────────────────────────────────────────────────

async def _cleanup_photo_counters(user_id: int, context: ContextTypes.DEFAULT_TYPE, keep_last: bool = True):
    """Delete intermediate photo counter messages, optionally keeping the last one."""
    msg_ids = state.get(user_id, "photo_msg_ids", []) or []
    if not msg_ids:
        return
    to_delete = msg_ids[:-1] if keep_last else msg_ids
    for mid in to_delete:
        try:
            await context.bot.delete_message(user_id, mid)
        except Exception:
            pass
    # Keep only the last one in state (if kept)
    state.set(user_id, "photo_msg_ids", msg_ids[-1:] if keep_last else [])


async def _run_analyze_core(user_id: int, context: ContextTypes.DEFAULT_TYPE, reply_func):
    lang = state.get_lang(user_id)
    # Cleanup intermediate photo counter messages, keep only last
    await _cleanup_photo_counters(user_id, context, keep_last=True)
    # Run date: today by default for photo (no auto-detect from Telegram-resized photos)
    run_date = state.get(user_id, "run_date") or datetime.now().date().isoformat()
    # Use ALL photos: approved + newly uploaded (not yet approved)
    _approved = state.get_approved(user_id) or []
    _pending = state.get_photos(user_id) or []
    # Dedupe while preserving order
    seen = set()
    photos = []
    for pid in _approved + _pending:
        if pid not in seen:
            seen.add(pid)
            photos.append(pid)

    if not photos:
        await reply_func(t(lang, "need_3"))
        return

    discipline = state.get_discipline(user_id)
    category = state.get_category(user_id)
    profile = get_user_profile(user_id)
    athlete_name = profile.get("athlete_name") if profile else None
    birth_year = profile.get("birth_year") if profile else None

    image_urls = [await get_file_url(context.bot, fid) for fid in photos]
    status_msg = await context.bot.send_message(user_id, t(lang, "analyzing"))

    track(user_id, "analysis_started", mode="photo", photos_count=len(image_urls), discipline=discipline, category=category)
    _t_start = datetime.now()
    try:
        result = await analyze_images(
            image_urls=image_urls,
            athlete_name=athlete_name,
            birth_year=birth_year,
            category=category,
            discipline=discipline,
            lang=lang,
            user_id=user_id,
        )
        formatted = format_analysis(result, lang=lang)
        # Extract score for structured storage + tracking
        _score = None
        try:
            import re as _re
            m = _re.search(r'(\d+(?:[.,]\d+)?)\s*/\s*10', formatted)
            if m: _score = float(m.group(1).replace(',', '.'))
        except Exception:
            pass
        analysis_id = save_analysis(
            telegram_user_id=user_id,
            photos_count=len(image_urls),
            result_text=formatted,
            status="success",
            score=_score,
            mode="photo",
            discipline=discipline,
            lang=lang,
            run_date=run_date,
        )
        state.set_last_analysis_id(user_id, analysis_id)
        try:
            await status_msg.delete()
        except Exception:
            pass
        await reply_func(formatted)
        _dur = (datetime.now() - _t_start).total_seconds()
        track(user_id, "analysis_completed", mode="photo", duration_sec=round(_dur,2), score=_score)

        pdf_path = await generate_pdf(user_id, formatted, lang=lang, run_date=run_date, report_date=datetime.now().date().isoformat())
        with open(pdf_path, "rb") as pdf:
            await context.bot.send_document(user_id, pdf)
        os.remove(pdf_path)

        await context.bot.send_message(
            user_id,
            t(lang, "rate"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👍", callback_data="fb_good"),
                InlineKeyboardButton("👎", callback_data="fb_bad"),
            ]])
        )
    except Exception as e:
        logger.exception(f"analysis_error {e}")
        save_analysis(telegram_user_id=user_id, photos_count=len(photos), result_text=str(e), status="error", mode="photo", discipline=discipline, lang=lang, run_date=run_date)
        track(user_id, "error", where="analysis_photo", message=str(e)[:300])
        await notify_owner(context, f"⚠️ <b>Photo analysis error</b>\nuser={user_id}\n<pre>{str(e)[:500]}</pre>")
        await reply_func(t(lang, "error", e=e))

    state.reset_session(user_id)


# ── FULL ANALYSIS (с quality check) ───────────────────────────────────────────

async def run_analyze(user_id: int, context: ContextTypes.DEFAULT_TYPE, reply_func):
    lang = state.get_lang(user_id)
    discipline = state.get_discipline(user_id)
    category = state.get_category(user_id)

    new_photos = state.get_photos(user_id)
    approved = state.get_approved(user_id)

    if not new_photos:
        if len(approved) >= 3:
            await _run_analyze_core(user_id, context, reply_func)
        else:
            await reply_func(t(lang, "need_3"))
        return

    checking_msg = await context.bot.send_message(user_id, t(lang, "checking"))

    image_urls = [await get_file_url(context.bot, fid) for fid in new_photos]
    quality_text = await check_images_quality(
        image_urls=image_urls, discipline=discipline, category=category, lang=lang, user_id=user_id
    )
    quality = parse_quality_result(quality_text)

    # Remove the "Reviewing photo quality..." message now that we have a verdict
    try:
        await checking_msg.delete()
    except Exception:
        pass

    if quality["bad_indexes"]:
        new_good = [p for i, p in enumerate(new_photos) if i not in quality["bad_indexes"]]
    elif quality["good_photos"] > 0:
        new_good = new_photos[:quality["good_photos"]]
    else:
        new_good = new_photos

    # REJECT
    if quality["status"] == "REJECT":
        issues_text = "\n".join(f"- {i}" for i in quality["issues"]) if quality["issues"] else ""
        state.set_photos(user_id, [])
        if len(approved) >= 3:
            await context.bot.send_message(
                user_id,
                t(lang, "rejected", issues=issues_text) + "\n\n" + t(lang, "can_use_approved", n=len(approved)),
                reply_markup=keyboard_add_or_analyze(lang)
            )
        else:
            await reply_func(t(lang, "rejected", issues=issues_text))
        return

    # добавляем хорошие к одобренным
    all_approved = approved + new_good
    state.set_approved(user_id, all_approved)
    state.set_photos(user_id, [])
    total_approved = len(all_approved)

    # NEED_MORE
    if quality["status"] == "NEED_MORE" or total_approved < 3:
        can_add = min(5 - total_approved, 3)
        missing_text = (t(lang, "missing_hdr") + "\n".join(f"- {m}" for m in quality["missing"])) if quality["missing"] else ""
        msg = t(lang, "need_more_ph", good=total_approved, total=len(new_photos) + len(approved), missing=missing_text)
        if can_add > 0:
            state.set_photo_limit(user_id, total_approved + can_add)
            msg += "\n" + t(lang, "can_add_more", n=can_add)
            await context.bot.send_message(user_id, msg, reply_markup=keyboard_add_or_analyze(lang))
        else:
            await reply_func(msg)
        return

    # WARNING
    if quality["missing"]:
        missing_text = "\n".join(f"- {m}" for m in quality["missing"])
        at_limit = total_approved >= 5
        await context.bot.send_message(
            user_id,
            t(lang, "warning_limit" if at_limit else "warning_ok",
              missing=missing_text, left=max(0, 5 - total_approved)),
            reply_markup=keyboard_warning(lang, at_limit=at_limit)
        )
        return

    # OK
    await _run_analyze_core(user_id, context, reply_func)


# ── COMMAND /analyze ───────────────────────────────────────────────────────────

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not await require_access(update, context):
        return
    await run_analyze(user_id, context, update.message.reply_text)


# ── CALLBACKS ──────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not await require_access(update, context):
        return

    lang = state.get_lang(user_id)
    data = query.data

    # Date change UI
    if data == "date_change":
        await query.edit_message_text(
            t(lang, "ask_date"),
            reply_markup=keyboard_date_presets(lang),
        )
        return
    if data.startswith("date_set_"):
        try:
            days_ago = int(data.split("_")[-1])
        except ValueError:
            return
        from datetime import date as _date, timedelta as _td
        new_date = (_date.today() - _td(days=days_ago)).isoformat()
        state.set(user_id, "run_date", new_date)
        track(user_id, "run_date_changed", run_date=new_date, days_ago=days_ago, source="preset")
        _ds = new_date[8:10] + "." + new_date[5:7] + "." + new_date[0:4]
        # Redraw original message with new date + before-analyze keyboard
        _kb_before = keyboard_before_analyze(lang)
        _rows = list(_kb_before.inline_keyboard) + [
            [InlineKeyboardButton(t(lang, "change_date_btn"), callback_data="date_change")]
        ]
        kb_combined = InlineKeyboardMarkup(_rows)
        await query.edit_message_text(
            t(lang, "video_saved") + "\n" + t(lang, "date_saved", date=_ds) + "\n\n" + t(lang, "ask_before_analyze"),
            reply_markup=kb_combined
        )
        return

    # Open calendar at current month
    if data == "cal_open":
        from datetime import date as _date
        today = _date.today()
        await query.edit_message_text(
            t(lang, "cal_title"),
            reply_markup=keyboard_calendar(today.year, today.month, lang),
        )
        return

    # Navigate calendar to another month
    if data.startswith("cal_nav_"):
        parts = data.split("_")
        try:
            y = int(parts[2]); m = int(parts[3])
        except (IndexError, ValueError):
            return
        # Clamp reasonable range (no far past/future)
        from datetime import date as _date
        today = _date.today()
        min_year = today.year - 5
        max_year = today.year + 1
        if y < min_year or y > max_year:
            return
        await query.edit_message_reply_markup(
            reply_markup=keyboard_calendar(y, m, lang)
        )
        return

    # Empty / label cells — ignore
    if data == "cal_ignore":
        return

    # Pick a specific date from calendar
    if data.startswith("date_pick_"):
        iso = data[len("date_pick_"):]
        # Validate basic format
        if len(iso) == 10 and iso[4] == "-" and iso[7] == "-":
            state.set(user_id, "run_date", iso)
            from datetime import date as _date
            try:
                picked = _date.fromisoformat(iso)
                days_ago = (_date.today() - picked).days
            except Exception:
                days_ago = None
            track(user_id, "run_date_changed", run_date=iso, days_ago=days_ago, source="calendar")
            _ds = iso[8:10] + "." + iso[5:7] + "." + iso[0:4]
            _kb_before = keyboard_before_analyze(lang)
            _rows = list(_kb_before.inline_keyboard) + [
                [InlineKeyboardButton(t(lang, "change_date_btn"), callback_data="date_change")]
            ]
            kb_combined = InlineKeyboardMarkup(_rows)
            await query.edit_message_text(
                t(lang, "video_saved") + "\n" + t(lang, "date_saved", date=_ds) + "\n\n" + t(lang, "ask_before_analyze"),
                reply_markup=kb_combined
            )
        return

    # Admin panel entry point
    if data == "admin_panel":
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            await query.answer(t(lang, "no_access"), show_alert=True)
            return
        await query.edit_message_text(
            t(lang, "admin_title"),
            parse_mode="HTML",
            reply_markup=keyboard_admin_panel(lang),
        )
        return

    # Admin panel: close (delete message)
    if data == "admin_back":
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            return
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # Admin panel: stats (1d / 7d / 30d)
    if data.startswith("admin_stats_"):
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            await query.answer(t(lang, "no_access"), show_alert=True)
            return
        try:
            days = int(data.split("_")[-1])
        except ValueError:
            return
        title_map = {1: "📊 Stats · 24h", 7: "📊 Stats · 7 days", 30: "📊 Stats · 30 days"}
        text = _build_stats_report(days, title_map.get(days, "📊 Stats"))
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "adm_back"), callback_data="admin_panel")
            ]]),
        )
        return

    if data == "admin_retention":
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            await query.answer(t(lang, "no_access"), show_alert=True)
            return
        await query.edit_message_text(
            _build_retention_report(), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "adm_back"), callback_data="admin_panel")
            ]]),
        )
        return

    if data == "admin_errors":
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            await query.answer(t(lang, "no_access"), show_alert=True)
            return
        await query.edit_message_text(
            _build_errors_report(), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "adm_back"), callback_data="admin_panel")
            ]]),
        )
        return

    if data == "admin_pending":
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            await query.answer(t(lang, "no_access"), show_alert=True)
            return
        await query.edit_message_text(
            _build_pending_report(), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "adm_back"), callback_data="admin_panel")
            ]]),
        )
        return

    # Access-request approval buttons (owner only)
    if data.startswith("allow_") or data.startswith("deny_"):
        if user_id != OWNER_ID and user_id not in ALLOWED_USERS:
            await query.answer("Not authorized", show_alert=True)
            return
        try:
            target = int(data.split("_", 1)[1])
        except Exception:
            await query.answer("Bad payload", show_alert=True)
            return
        if data.startswith("allow_"):
            changed = approve_user(target, approved_by=user_id)
            if changed:
                try:
                    await context.bot.send_message(
                        target,
                        "✓ Доступ открыт! Нажми /start чтобы начать.\n✓ Access granted! Tap /start to begin."
                    )
                except Exception as e:
                    logger.warning(f"welcome to {target} failed: {e}")
            track(user_id, "user_approved", target=target, via="button")
            try:
                await query.edit_message_text(f"✓ Allowed <code>{target}</code>", parse_mode="HTML")
            except Exception:
                pass
        else:
            changed = deny_user(target, denied_by=user_id)
            track(user_id, "user_denied", target=target, via="button")
            try:
                await query.edit_message_text(f"✗ Denied <code>{target}</code>", parse_mode="HTML")
            except Exception:
                pass
        return

    if data == "start_flow":
        state.reset_session(user_id)
        await query.edit_message_text(t(lang, "choose_mode"), reply_markup=keyboard_mode(lang))

    elif data in ("mode_quick", "mode_detailed"):
        mode = "quick" if data == "mode_quick" else "detailed"
        state.set_analysis_mode(user_id, mode)
        track(user_id, "mode_selected", mode=mode)
        state.set_state(user_id, "waiting_name")
        await query.edit_message_text(t(lang, "ask_name"))

    elif data in ("disc_sl", "disc_gs"):
        disc = "SL" if data == "disc_sl" else "GS"
        state.set_discipline(user_id, disc)
        track(user_id, "discipline_selected", discipline=disc)
        state.set_photos(user_id, [])
        state.set_approved(user_id, [])
        state.set_photo_limit(user_id, 5)
        mode = state.get_analysis_mode(user_id)
        if mode == "detailed":
            state.set_state(user_id, "waiting_run_type")
            await query.edit_message_text(
                t(lang, "ask_run_type"),
                reply_markup=keyboard_run_type(lang)
            )
        else:
            state.set_state(user_id, "waiting_photos")
            await query.edit_message_text(t(lang, "disc_chosen", disc=disc))

    elif data in ("run_type_training", "run_type_race"):
        run_type = "training" if data == "run_type_training" else "race"
        state.set_run_type(user_id, run_type)
        track(user_id, "run_type_selected", run_type=run_type)
        state.set_state(user_id, "waiting_video")
        await query.edit_message_text(t(lang, "ask_video"))

    elif data == "add_photos_before":
        state.set_state(user_id, "waiting_extra_photos")
        await query.edit_message_text(t(lang, "send_photos_hint"))
        await context.bot.send_message(
            user_id,
            t(lang, "send_photos_prompt"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "btn_analyze_now"), callback_data="analyze_now")
            ]])
        )

    elif data == "upload_new_video":
        old_video = state.get_video_path(user_id)
        if old_video and os.path.exists(old_video):
            os.remove(old_video)
        state.set_video_path(user_id, None)
        state.set_state(user_id, "waiting_video")
        await query.edit_message_text(t(lang, "new_video_prompt"))

    elif data == "more_extra":
        await query.edit_message_text(t(lang, "wait_more_photos"))

    elif data == "analyze_now":
        await query.edit_message_text("⏳ " + t(lang, "analyzing_video"))
        video_status_msg = query.message  # reference for deletion later
        # Cleanup intermediate extra-photo counter messages (keep the last — now shows "⏳")
        await _cleanup_photo_counters(user_id, context, keep_last=True)

        video_path   = state.get_video_path(user_id)
        extra_photos = state.get_photos(user_id)
        discipline   = state.get_discipline(user_id)
        run_type     = state.get_run_type(user_id)
        run_date     = state.get(user_id, "run_date") or datetime.now().date().isoformat()

        if not video_path:
            await context.bot.send_message(user_id, t(lang, "video_error", e="video not found"))
            state.reset_session(user_id)
            return

        extra_urls = None
        if extra_photos:
            extra_urls = []
            for fid in extra_photos:
                f = await context.bot.get_file(fid)
                if f.file_path.startswith("https://"):
                    extra_urls.append(f.file_path)
                else:
                    extra_urls.append(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}")

        frame_paths = []
        try:
            _vlock = _get_video_lock(user_id)
            if _vlock.locked():
                await context.bot.send_message(user_id, "⏳ Уже идёт анализ вашего видео. Подождите окончания." if lang == "ru" else "⏳ Your video is already being analysed. Please wait.")
                return
            async with _vlock:
                async with _video_sem:
                    track(user_id, "analysis_started", mode="video", discipline=discipline, run_type=run_type, extra_photos=len(extra_urls or []))
                    _t_start_v = datetime.now()
                    analysis_text, frame_paths = await asyncio.to_thread(
                        analyze_video, video_path, run_type, discipline, lang, OPENAI_API_KEY, extra_urls, "U12", user_id, run_date
                    )
                    _dur_v = (datetime.now() - _t_start_v).total_seconds()

            # Delete status message before sending results
            try:
                await video_status_msg.delete()
            except Exception:
                pass

            # DEBUG: dump raw GPT text for parser diagnosis
            print(f"[GPT_RAW] frame_paths={frame_paths}", flush=True)
            print(f"[GPT_RAW] text_head=\n{analysis_text[:2000]}", flush=True)

            # Format for Telegram (summary only, no frame-by-frame)
            formatted = format_analysis(analysis_text, lang=lang)
            await context.bot.send_message(user_id, formatted, parse_mode="HTML")

            # Build and send detailed PDF
            profile = get_user_profile(user_id)
            athlete_name  = (profile.get("athlete_name") or "-") if profile else "-"
            birth_year_val = str(profile.get("birth_year") or "-") if profile else "-"
            category_val   = state.get_category(user_id) or "-"

            pdf_data = parse_video_analysis(
                text=analysis_text,
                athlete=athlete_name,
                birth_year=birth_year_val,
                category=category_val,
                discipline=discipline,
                run_type=run_type,
                frame_paths=frame_paths,
                lang=lang,
            )
            # Inject run_date and report_date for PDF hero/footer
            pdf_data["run_date"] = run_date
            pdf_data["report_date"] = datetime.now().date().isoformat()
            logger.info(f"[analyze_now] pdf_data={pdf_data}")
            logger.info(
                f"PARSED_DATA: score={pdf_data.get('score')} "
                f"phases={pdf_data.get('phase_scores')} "
                f"strengths_count={len(pdf_data.get('strengths', []))} "
                f"drills_count={len(pdf_data.get('drills', []))} "
                f"frames_count={len([p for p in [ph.get('frame_path') for ph in pdf_data.get('phases', [])] if p])}"
            )
            _score_v = None
            try:
                _score_v = float(str(pdf_data.get('score', '') or '').replace('/10', '').strip() or 0) or None
            except Exception:
                pass
            track(user_id, "analysis_completed", mode="video", duration_sec=round(_dur_v, 2), score=_score_v, frames=len(frame_paths))
            # Persist the analysis in DB so feedback can link to it
            try:
                _analysis_id_v = save_analysis(
                    telegram_user_id=user_id,
                    photos_count=len(frame_paths) + len(extra_urls or []),
                    result_text=(analysis_text or "")[:500],
                    status="success",
                    score=_score_v,
                    mode="video",
                    discipline=discipline,
                    lang=lang,
                    run_date=run_date,
                )
                state.set_last_analysis_id(user_id, _analysis_id_v)
            except Exception as _se:
                logger.warning(f"video save_analysis failed: {_se}")
            pdf_path = await generate_pdf_detailed(user_id, pdf_data, lang=lang)
            with open(pdf_path, "rb") as pdf_file:
                await context.bot.send_document(user_id, pdf_file)
            os.remove(pdf_path)
            # Feedback prompt (like in photo flow)
            await context.bot.send_message(
                user_id,
                t(lang, "rate"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👍", callback_data="fb_good"),
                    InlineKeyboardButton("👎", callback_data="fb_bad"),
                ]])
            )

        except Exception as e:
            logger.exception(f"video_analysis_error {e}")
            track(user_id, "error", where="analysis_video", message=str(e)[:300])
            await notify_owner(context, f"⚠️ <b>Video analysis error</b>\nuser={user_id}\n<pre>{str(e)[:500]}</pre>")
            await context.bot.send_message(user_id, t(lang, "video_error", e=e))
        finally:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
            for fp in frame_paths:
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception:
                    pass

        state.reset_session(user_id)
        # 'analysis_complete' message removed — PDF + feedback buttons already signal completion

    elif data == "add_more":
        approved = len(state.get_approved(user_id))
        new = len(state.get_photos(user_id))
        total = approved + new
        await query.edit_message_text(t(lang, "add_more", n=total))

    elif data == "restart":
        state.reset_session(user_id)
        state.set_state(user_id, "waiting_photos")
        await query.edit_message_text(t(lang, "restart"))

    elif data == "do_analyze":
        await query.edit_message_text("⏳")
        async def reply_func(text):
            await context.bot.send_message(user_id, text, parse_mode='HTML')
        await run_analyze(user_id, context, reply_func)
        try:
            await query.message.delete()
        except Exception:
            pass

    elif data == "do_analyze_confirmed":
        await query.edit_message_text("⏳")
        async def reply_func(text):
            await context.bot.send_message(user_id, text, parse_mode='HTML')
        await _run_analyze_core(user_id, context, reply_func)
        try:
            await query.message.delete()
        except Exception:
            pass

    elif data == "fb_good":
        analysis_id = state.get_last_analysis_id(user_id)
        save_feedback(user_id, analysis_id, "positive")
        track(user_id, "feedback", type="positive", analysis_id=analysis_id)
        await query.edit_message_text(t(lang, "thanks_good"))

    elif data == "fb_bad":
        analysis_id = state.get_last_analysis_id(user_id)
        save_feedback(user_id, analysis_id, "negative")
        track(user_id, "feedback", type="negative", analysis_id=analysis_id)
        state.set_state(user_id, "waiting_feedback")
        await query.edit_message_text(t(lang, "ask_bad"))


# ── VIDEO HANDLER ──────────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not await require_access(update, context):
        return

    lang = state.get_lang(user_id)
    cur_state = state.get_state(user_id)

    if cur_state != "waiting_video":
        return

    video = update.message.video or update.message.document
    if video is None:
        return

    duration = getattr(video, "duration", None)
    if duration and duration > 60:
        await update.message.reply_text(t(lang, "video_too_long"))
        return

    tmp_path = f"/tmp/skibot_{user_id}.mp4"
    try:
        tg_file = await context.bot.get_file(video.file_id)
        await tg_file.download_to_drive(tmp_path)
    except Exception as e:
        logger.exception(f"video_download_error {e}")
        track(user_id, "error", where="video_download", message=str(e)[:300])
        await notify_owner(context, f"⚠️ <b>Video download error</b>\nuser={user_id}\n<pre>{str(e)[:500]}</pre>")
        await update.message.reply_text(t(lang, "video_error", e=e))
        return

    # ── Video quality pre-check ─────────────────────────────────────────────
    try:
        qr = await asyncio.to_thread(analyze_video_quality, tmp_path)
        score_str = f"{qr['score']:.1f}/10"
        msg_body  = qr["message_ru"] if lang == "ru" else qr["message_en"]
        quality_msg = f"📊 {'Оценка видео' if lang == 'ru' else 'Video score'}: {score_str} — {msg_body}"

        if qr["status"] == "BAD":
            await update.message.reply_text(quality_msg)
            os.remove(tmp_path)
            return

        await update.message.reply_text(quality_msg)
    except Exception as e:
        logger.warning(f"video_quality_check_failed {e}")
        track(user_id, "error", where="video_quality", message=str(e)[:300])
        # Non-fatal — continue without quality gating

    state.set_video_path(user_id, tmp_path)
    state.set_photos(user_id, [])
    state.set_state(user_id, "waiting_extra_photos")
    try:
        _sz = os.path.getsize(tmp_path)
    except Exception:
        _sz = None
    track(user_id, "video_uploaded", size_bytes=_sz)
    # Extract run_date from video metadata (ffprobe)
    _detected = extract_run_date(tmp_path)
    from datetime import date as _date
    if _detected:
        state.set(user_id, "run_date", _detected)
        _date_str = _detected[8:10] + "." + _detected[5:7] + "." + _detected[0:4]
        date_line = t(lang, "date_saved_auto", date=_date_str)
        track(user_id, "run_date_detected", run_date=_detected, source="ffprobe")
    else:
        today = _date.today().isoformat()
        state.set(user_id, "run_date", today)
        _date_str = today[8:10] + "." + today[5:7] + "." + today[0:4]
        date_line = t(lang, "date_saved_default", date=_date_str)
        track(user_id, "run_date_detected", run_date=today, source="default")
    # Combined keyboard: before-analyze actions + change date option
    _kb_before = keyboard_before_analyze(lang)
    _rows = list(_kb_before.inline_keyboard) + [
        [InlineKeyboardButton(t(lang, "change_date_btn"), callback_data="date_change")]
    ]
    kb_combined = InlineKeyboardMarkup(_rows)
    await update.message.reply_text(
        t(lang, "video_saved") + "\n" + date_line + "\n\n" + t(lang, "ask_before_analyze"),
        reply_markup=kb_combined
    )


# ── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("errors", errors_cmd))
    app.add_handler(CommandHandler("user", user_cmd))
    app.add_handler(CommandHandler("retention", retention_cmd))
    app.add_handler(CommandHandler("allow", allow_cmd))
    app.add_handler(CommandHandler("deny", deny_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.add_handler(CallbackQueryHandler(handle_callback))

    async def set_commands(app):
        # Default menu (everyone sees this)
        await app.bot.set_my_commands(
            [BotCommand("start", "Start analysis")],
            language_code="en",
        )
        await app.bot.set_my_commands(
            [BotCommand("start", "Начать анализ")],
            language_code="ru",
        )
        await app.bot.set_my_commands(
            [BotCommand("start", "Начать анализ / Start analysis")],
        )
        # Admin-scoped menu (only OWNER sees these in slash menu)
        try:
            admin_cmds = [
                BotCommand("start", "Начать анализ / Start analysis"),
                BotCommand("admin", "Админ-панель / Admin panel"),
            ]
            await app.bot.set_my_commands(
                admin_cmds,
                scope=BotCommandScopeChat(chat_id=OWNER_ID),
            )
            print(f"Admin menu set for OWNER_ID={OWNER_ID}")
        except Exception as e:
            print(f"Admin menu setup failed: {e}")
    app.post_init = set_commands

    # Daily report at 09:00 MSK
    try:
        jq = app.job_queue
        if jq is not None:
            report_time = _dtime(hour=9, minute=0, tzinfo=_MSK) if _MSK else _dtime(hour=6, minute=0)  # 09:00 MSK ~ 06:00 UTC
            jq.run_daily(daily_report_job, time=report_time, name="daily_report")
            print(f"Daily report scheduled for 09:00 MSK (owner={OWNER_ID})")
            # Weekly on Mondays at 09:05 MSK
            jq.run_daily(weekly_report_job, time=_dtime(hour=9, minute=5, tzinfo=_MSK) if _MSK else _dtime(hour=6, minute=5), days=(0,), name="weekly_report")
            print("Weekly report scheduled for Monday 09:05 MSK")
    except Exception as e:
        print(f"Failed to schedule daily report: {e}")


    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
