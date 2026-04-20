import asyncio
from openai import AsyncOpenAI
from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.utils.openai_tracking import log_openai_usage
import time as _time

client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def get_age_mode(category: str) -> str:
    scoring_scale = """
ШКАЛА ОЦЕНОК (привязана к возрастной категории, не к абсолютному уровню):
10 - эталонная техника для этой категории, практически нет замечаний
9  - очень сильная техника, единичные мелкие недочёты
8  - выше среднего для категории, есть 1-2 заметных момента
7  - средний уровень для категории, техника рабочая но есть зоны роста
6  - чуть ниже среднего, базовые элементы есть но нестабильны
5  - ниже среднего, ошибки влияют на результат
4  - техника только формируется, много нестабильности
1-3 - начальный уровень

ВАЖНО: оценка 8 для U8 и оценка 8 для U14 — совершенно разные уровни абсолютно.
Оценивай ТОЛЬКО относительно эталона своей категории.
Допускаются оценки с шагом 0.5 (например 7.5, 8.5).
"""

    if category == "U8":
        return scoring_scale + """
Режим: U8 (7 лет и младше)

Эталон (10/10) = лучший U8 в регионе: уверенно едет на кантах, агрессивно
атакует трассу, хорошая стойка, нет страха на скорости.

Что оцениваешь и как:

КАНТОВАНИЕ: есть ли попытка кантования, угол канта — не требуй стабильности
как у U10, смотри на наличие и агрессивность

ДАВЛЕНИЕ: общее ощущение внешней лыжи, не осознанное давление через дугу —
достаточно если ребёнок интуитивно загружает внешнюю

СТОЙКА И КОРПУС: базовая атлетическая стойка, смелость, взгляд вперёд —
разделение корпуса не критично в этом возрасте

ЛИНИЯ: проходит ли ворота, общее направление движения — не требуй
тактического планирования

СКОРОСТЬ И УВЕРЕННОСТЬ: нет ли страха, едет ли агрессивно — это важный
критерий для U8, многие сильные дети именно этим отличаются

Топовый U8 может ехать очень уверенно на кантах — не занижай оценку только
из-за возраста. Если техника явно сильная для возраста — ставь 9-10.

Диапазон большинства активных U8: 5-8.
Формулировки: позитивные, акцент на прогрессе и смелости.
Избегай технических терминов которые непонятны родителям маленьких детей.
"""

    elif category in ["U10", "U12"]:
        return scoring_scale + """
Режим: ДЕТСКИЙ СПОРТ (U10-U12)

- Эталон (10/10) = призёр регионального старта своей категории
- Главный фокус: кантование, давление на внешнюю лыжу, базовая линия
- Начинаем смотреть на разделение корпуса — но не требуем совершенства
- Большинство соревновательных детей попадает в 5-8
- Подчёркивай прогресс и потенциал, не перегружай критикой
"""
    elif category in ["U14", "U16"]:
        return scoring_scale + """
Режим: ЮНИОР (U14-U16)

- Эталон (10/10) = топ юниор страны / призёр юниорского чемпионата
- Смотри на технику и эффективность в трассе
- Ошибки уже существенно влияют на результат
- Большинство соревновательных юниоров — 6-8
"""
    elif category == "U18":
        return scoring_scale + """
Режим: СТАРШИЙ ЮНИОР (U18)

- Эталон (10/10) = уровень национальной сборной юниоров
- Оценивай эффективность, стабильность и скорость
- Можно сравнивать с сильными race-спортсменами (не элита WC)
- Диапазон для сильных спортсменов — 7-9
"""
    else:
        return scoring_scale + """
Режим: ВЗРОСЛЫЙ СПОРТ

- Эталон (10/10) = уровень сильного клубного / любительского race-спортсмена
- Не используй детские формулировки
- Фокус на эффективности, стабильности и применимости техники
- Допускается сравнение с race-техникой высокого уровня (не элита WC)
"""


async def analyze_images(image_urls, athlete_name=None, birth_year=None, category=None, discipline="GS", lang="ru", user_id=None, run_date=None):
    intro_parts = []

    if lang == "en":
        if athlete_name: intro_parts.append(f"Name: {athlete_name}")
        if birth_year:   intro_parts.append(f"Year of birth: {birth_year}")
        if category:     intro_parts.append(f"Category: {category}")
        if discipline:   intro_parts.append(f"Discipline: {discipline}")
        if run_date:     intro_parts.append(f"Run date: {run_date}")
    else:
        if athlete_name: intro_parts.append(f"Имя: {athlete_name}")
        if birth_year:   intro_parts.append(f"Год рождения: {birth_year}")
        if category:     intro_parts.append(f"Категория: {category}")
        if discipline:   intro_parts.append(f"Дисциплина: {discipline}")
        if run_date:     intro_parts.append(f"Дата заезда: {run_date}")

    intro_text = "\n".join(intro_parts)
    age_mode = get_age_mode(category)

    # ── КРИТЕРИИ И ОЦЕНКИ по категории ────────────────────────────────────────
    is_u8 = category == "U8"
    is_young = category in ["U10", "U12"]
    is_junior = category in ["U14", "U16"]
    is_senior = category in ["U18", "Adult"]

    if is_u8:
        if lang == "en":
            discipline_block = f"""
{discipline} analysis focus for U8:
- confidence and fearlessness on course
- basic athletic stance on skis
- ski control (turning, direction changes)
- gate line (passes gates without stopping)
- boldness and speed comfort
Do NOT assess: carving quality, outside ski pressure, body separation - these are not U8 criteria.
"""
            detailed_scores = """
🔹 Confidence - X/10
🔹 Stance - X/10
🔹 Ski control - X/10
🔹 Gate line - X/10
🔹 Boldness - X/10
"""
        else:
            discipline_block = f"""
Фокус анализа {discipline} для U8:
- уверенность и бесстрашие на трассе
- базовая атлетическая стойка на лыжах
- управление лыжами (повороты, смена направления)
- линия (проходит ворота не останавливаясь)
- смелость и комфорт на скорости
НЕ оценивай: качество карвинга, давление на внешнюю лыжу, разделение корпуса — это не критерии U8.
"""
            detailed_scores = """
🔹 Уверенность - X/10
🔹 Стойка - X/10
🔹 Управление лыжами - X/10
🔹 Линия - X/10
🔹 Смелость - X/10
"""

    elif discipline == "SL":
        if lang == "en":
            discipline_block = "SL analysis focus:\n- gate line\n- rhythm and transitions\n- early entry\n- outside ski work\n- arm and body position\n- speed in short arcs\n"
            if is_young:
                detailed_scores = "🔹 Gate line - X/10\n🔹 Rhythm - X/10\n🔹 Outside ski - X/10\n🔹 Balance - X/10\n🔹 Body position - X/10\n"
            else:
                detailed_scores = "🔹 Gate line - X/10\n🔹 Rhythm - X/10\n🔹 Outside ski pressure - X/10\n🔹 Speed efficiency - X/10\n🔹 Body/arm position - X/10\n"
        else:
            discipline_block = "Фокус анализа SL:\n- линия между воротами\n- ритм и переходы\n- ранний вход в поворот\n- работа внешней лыжи\n- положение рук и корпуса\n- сохранение скорости\n"
            if is_young:
                detailed_scores = "🔹 Линия в трассе - X/10\n🔹 Ритм поворотов - X/10\n🔹 Работа внешней лыжи - X/10\n🔹 Баланс - X/10\n🔹 Положение корпуса - X/10\n"
            else:
                detailed_scores = "🔹 Линия в трассе - X/10\n🔹 Ритм поворотов - X/10\n🔹 Давление на внешнюю - X/10\n🔹 Эффективность скорости - X/10\n🔹 Корпус и руки - X/10\n"

    else:  # GS
        if lang == "en":
            discipline_block = "GS analysis focus:\n- arc shape and quality\n- early edge\n- pressure on outside ski\n- balance at speed\n- line through gates\n- body and leg position\n"
            if is_young:
                detailed_scores = "🔹 Arc line - X/10\n🔹 Edging - X/10\n🔹 Balance - X/10\n🔹 Outside ski pressure - X/10\n🔹 Body position - X/10\n"
            elif is_junior:
                detailed_scores = "🔹 Arc line - X/10\n🔹 Outside ski pressure - X/10\n🔹 Edging - X/10\n🔹 Body position - X/10\n🔹 Speed - X/10\n"
            else:
                detailed_scores = "🔹 Arc line - X/10\n🔹 Outside ski pressure - X/10\n🔹 Edging - X/10\n🔹 Speed efficiency - X/10\n🔹 Stability - X/10\n"
        else:
            discipline_block = "Фокус анализа GS:\n- форма и чистота дуги\n- ранний кант\n- давление на внешнюю лыжу\n- баланс на скорости\n- линия прохождения\n- положение корпуса и ног\n"
            if is_young:
                detailed_scores = "🔹 Линия дуги - X/10\n🔹 Кантование - X/10\n🔹 Баланс - X/10\n🔹 Давление на лыжу - X/10\n🔹 Положение корпуса - X/10\n"
            elif is_junior:
                detailed_scores = "🔹 Линия дуги - X/10\n🔹 Давление на лыжу - X/10\n🔹 Кантование - X/10\n🔹 Положение корпуса - X/10\n🔹 Скорость - X/10\n"
            else:
                detailed_scores = "🔹 Линия дуги - X/10\n🔹 Давление на лыжу - X/10\n🔹 Кантование - X/10\n🔹 Эффективность - X/10\n🔹 Стабильность - X/10\n"

    if lang == "en":
        prompt = f"""
You are a professional alpine skiing coach of international level.
You are making a technical review for the athlete and their parents.

IMPORTANT: Write your ENTIRE response in ENGLISH. All text, analysis, recommendations - everything must be in English only.

{intro_text}

SCORING SCALE (relative to age category, not absolute level):
10 - perfect technique for this category
9  - very strong, minor issues only
8  - above average, 1-2 noticeable moments
7  - average level for category, working technique with growth areas
6  - slightly below average, basics present but unstable
5  - below average, errors affect results
4  - technique forming, much instability
1-3 - beginner level

{"U8 note: focus on confidence, stance, ski control, boldness. Do NOT assess carving, outside ski pressure, body separation." if is_u8 else ""}
{"U10/U12 note: assess relative to top regional racers of same age." if is_young else ""}
{"U14/U16 note: assess relative to top national junior level." if is_junior else ""}
{"U18/Adult note: assess relative to strong club/amateur race level." if is_senior else ""}

{discipline_block}

STRICT PHOTO ANALYSIS CONSTRAINTS

✅ WHITELIST — can assess from photos:
- Basic stance (leg width, knee bend, center of gravity height)
- Body position (lean, shoulder rotation, chest direction)
- Angulation (hip-to-slope angle, upper/lower body separation)
- Balance (weight distribution — visible from CoG position over skis)
- Arm position (placement, forward reach, height)
- Edging (ski-to-snow angle, outside/inside ski difference)
- Line in course (position relative to gates — high/low/on line)
- Head position and gaze direction

❌ BLACKLIST — FORBIDDEN to assess from photos:
- Turn transitions (requires frame sequence)
- Tempo and rhythm (requires video)
- Speed (impossible from static image)
- Pole timing (requires dynamics)
- Pressure dynamics (weight transfer over time)
- Time on flat skis (neutral phase)
- Acceleration/deceleration
- Foot and ankle work (almost always hidden by boot)

RULE: If observation belongs to BLACKLIST — DO NOT include it. Better fewer reliable points than many dubious ones.

Phase scores: DO NOT score "Transition" — use "—". Only score Entry, Apex, Exit on static aspects.

9. CONFIDENCE LEVEL
For each observation in growth areas indicate confidence:
  🟢 High — clearly visible in photo (position, angle, stance)
  🟡 Medium — partially visible (one angle, distant, backlit)
  🔴 Low — not directly visible, indirect conclusion
If photo is distant or poorly lit — DO NOT use 🟢 for details.

IMPORTANT:
- Write ONLY in English
- Do not invent anything not visible in photos
- Write clearly, without unnecessary words
- Do NOT use markdown: no **, --, ##, ---
- Scores can use 0.5 steps (e.g. 7.5, 8.5)

Return the answer strictly in this format:

🏔 Technique Analysis {discipline}

👤 Name: ...
🎂 Year of birth: ...
🏷 Category: ...

📊 Overall score: X / 10

{detailed_scores}

✅ Strengths:
• key strength — brief reason why it matters for skiing technique
• strength — brief reason
• strength — brief reason
(Each line: what + why it matters. Keep under 130 characters. No padding, no marketing.)

⚠️ Areas for growth:
• 🟠 [main growth area — what exactly and why critical]
• 🟡 [second growth area — what exactly and why important]
• [third growth area — no emoji, just what]

MARKER RULES (MANDATORY):
- ONLY ONE marker per line, and ONLY at the very beginning
- First line: starts with "🟠 " then text
- Second line: starts with "🟡 " then text
- Third line: NO emoji at start, just text
- Do NOT add any emoji in the middle or at the end of a line
- Do NOT use words "KEY"/"SECONDARY"/"ADDITIONAL" — the marker is enough
- Do NOT use 🟢 🔴 🟣 🟤 or any other colored circles — only 🟠 and 🟡
- Do NOT append "(🟢)", "(🟡)", "(🟠)" at end of lines — that's garbage

🎯 Drills (exactly 3)
Format for EACH:
[number]. [Name — specific action, NOT text from growth areas]
  ▸ Action: [how to perform]
  ▸ Focus: [what to pay attention to]
  ▸ Success: [marker of correct execution]

FORBIDDEN:
- Copying text from ⚠️ Growth areas into drill name
- Writing cause-effect chains instead of exercises
- Truncating text — each field must be complete

📈 Potential:
Exactly 3 short lines starting with " - ":
 - Strong elements for age: [what is genuinely above age norm — no grand claims, no "top region", no "FIS level"]
 - Next development step: [one concrete skill to work on next]
 - Main growth reserve: [the single change that would give the biggest jump]
(Each line: 1 sentence, coach-like, honest. Avoid: "top level", "leading level", "FIS", loud forecasts. OK: "solid base for age", "creates good foundation for next level", "main growth reserve".)


INTEGRITY RULES:
- Avoid false precision: do not write percentages or numeric values that cannot be measured from photos
- If an element is listed as a strength, do not list it as a growth area
- Rank growth areas by importance: key, secondary, additional
Additional:
- Score must match the category and scoring scale above
- For U8: use simple words, focus on boldness and progress, no technical jargon
- For adults: do not use children's language
- Do NOT use markdown: no **, --, ##, ---
{"- Do NOT mention: carving quality, outside ski pressure, body separation (not U8 criteria)" if is_u8 else ""}
"""
    else:
        prompt = f"""
Ты профессиональный тренер по горным лыжам международного уровня.

Ты делаешь разбор для спортсмена и его родителей.

{intro_text}

{age_mode}

{discipline_block}

ЖЁСТКИЕ ОГРАНИЧЕНИЯ ФОТО-АНАЛИЗА

✅ WHITELIST — можно оценивать по фото:
- Базовая стойка (ширина ног, сгибание коленей, высота центра тяжести)
- Положение корпуса (наклон, разворот плеч, направление грудной клетки)
- Ангуляция (угол бёдер к склону, разделение верх/низ тела)
- Баланс (распределение веса — видно по положению ЦТ над лыжами)
- Работа рук (положение, вынос вперёд, высота)
- Кантование (угол лыж к снегу, разница внешняя/внутренняя)
- Линия в трассе (позиция относительно ворот — высоко/низко/на линии)
- Положение головы и взгляд (направление)

❌ BLACKLIST — ЗАПРЕЩЕНО оценивать по фото:
- Переход между поворотами (нужна последовательность кадров)
- Темп и ритм (нужно видео)
- Скорость (невозможно определить по статичному изображению)
- Тайминг палки (нужна динамика)
- Динамика давления (перенос веса во времени)
- Время на плоских лыжах (нейтральная фаза)
- Ускорение/замедление
- Работа стопы и голеностопа (почти всегда скрыты ботинком)

ПРАВИЛО: Если наблюдение относится к BLACKLIST — НЕ ВКЛЮЧАЙ его в анализ. Лучше меньше пунктов, но достоверных.

Оценки по фазам: НЕ оценивай фазу "Переход" числом — поставь "—" (не доступно по фото). Оценивай только Вход, Апекс и Выход по статичным аспектам.

9. УРОВЕНЬ УВЕРЕННОСТИ
Для каждого наблюдения в зонах роста укажи уверенность:
  🟢 Высокая — чётко видно на фото (позиция, угол, стойка)
  🟡 Средняя — видно частично (один ракурс, далеко, контровой свет)
  🔴 Низкая — не видно напрямую, вывод косвенный
Если фото далеко или плохое освещение — НЕ СТАВЬ 🟢 для деталей.

ВАЖНО:
- Сначала оцени ТЕХНИКУ
- Затем оцени ЭФФЕКТИВНОСТЬ (скорость, сохранение энергии)
- Если техника неидеальна, но помогает ехать быстрее — отметь это
- Если техника мешает скорости — подчеркни это
- Не выдумывай, если что-то не видно на фото
- Пиши четко, без лишних слов
- Не используй markdown со звездочками

Верни ответ строго в формате:

🏔 Анализ техники {discipline}

👤 Имя: ...
🎂 Год рождения: ...
🏷 Категория: ...

📊 Общая оценка: X / 10

{detailed_scores}

✅ Сильные стороны:
• ключевой сильный элемент — коротко почему это важно для техники
• сильный элемент — коротко почему важно
• сильный элемент — коротко почему важно
(Каждая строка: что + почему важно. До 130 символов. Без рекламы, без общих слов.)

⚠️ Зоны роста:
• 🟠 [главная зона роста — что именно и почему критично]
• 🟡 [вторая зона роста — что именно и почему важно]
• [третья зона роста — без эмодзи, что именно]

ПРАВИЛА МАРКЕРОВ (ОБЯЗАТЕЛЬНО):
- ТОЛЬКО ОДИН маркер на строку, и ТОЛЬКО в самом начале
- Первая строка: начинается с "🟠 " и дальше текст
- Вторая строка: начинается с "🟡 " и дальше текст
- Третья строка: БЕЗ эмодзи в начале, просто текст
- НЕ добавляй эмодзи в середине или в конце строки
- НЕ используй слова "КЛЮЧЕВАЯ"/"ВТОРИЧНАЯ"/"ДОПОЛНИТЕЛЬНАЯ" — маркера достаточно
- НЕ используй 🟢 🔴 🟣 🟤 и любые другие цветные кружки — только 🟠 и 🟡
- НЕ добавляй "(🟢)", "(🟡)", "(🟠)" в конце строки — это мусор

🎯 Упражнения (ровно 3)
Формат КАЖДОГО:
[номер]. [Название — конкретное действие, НЕ текст из зон роста]
  ▸ Что делать: [описание выполнения]
  ▸ Фокус: [на что обращать внимание]
  ▸ Успех: [маркер правильного выполнения]

ЗАПРЕЩЕНО:
- Копировать текст из ⚠️ Зон роста в название
- Писать причинно-следственную цепочку вместо упражнения
- Обрывать текст — каждое поле завершённое предложение

📈 Потенциал:
Ровно 3 короткие строки, каждая начинается с " - ":
 - Сильные элементы для возраста: [что реально выше среднего для возраста — без громких фраз, без "топ региона", без "FIS-уровня"]
 - Следующий шаг развития: [один конкретный навык для работы]
 - Главный резерв роста: [одно изменение которое даст наибольший прирост]
(Каждая строка: 1 предложение, тренерский тон, честно. Избегай: "топ-уровень", "лидирующий уровень", "FIS", громкие прогнозы. Допустимо: "сильная база для возраста", "создаёт хорошую основу для следующего уровня", "главный резерв роста".)


ПРАВИЛА ДОСТОВЕРНОСТИ:
- Избегай ложной точности: не пиши проценты и числовые значения которые невозможно измерить по фото
- Если элемент назван сильной стороной, не называй его зоной роста
- Зоны роста ранжируй по важности: ключевая, вторичная, дополнительная
Дополнительно:
- Оценка должна соответствовать категории и шкале из инструкции
- Для U8: используй простые слова понятные родителям, акцент на смелости и прогрессе
- Для детей не занижай и не завышай сильно
- Для взрослых не используй детские формулировки
- НЕ используй markdown: никаких **, --, ##, ---
- Ответ должен хорошо читаться в Telegram
{"- Для U8: не упоминай технические термины (карвинг, давление, разделение корпуса)" if is_u8 else ""}
"""

    _t0 = _time.time()
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=1500,
        temperature=0.3,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *[
                    {"type": "image_url", "image_url": {"url": url}}
                    for url in image_urls
                ]
            ]
        }]
    )
    log_openai_usage(user_id, OPENAI_MODEL, response, purpose="photo_analysis", latency_sec=_time.time() - _t0)

    return response.choices[0].message.content


async def check_images_quality(image_urls, discipline=None, category=None, lang="ru", user_id=None):
    if lang == "en":
        prompt = f"""
You are an alpine skiing coach.

Check this set of photos before analysis.

Discipline: {discipline}
Category: {category}

Assess:
1. Discipline match (SL / GS)
2. Sufficient photos (minimum 3 good photos)
3. Quality (blur, distance)
4. Technique visibility (legs, body, skis)
5. Consistency (same athlete)
6. Outliers (bad photos)
7. Age match (approximate)

IMPORTANT:
- Do NOT require perfection
- Goal: can a RELIABLE analysis be done

Reply strictly in this format:

STATUS: OK / WARNING / NEED_MORE / REJECT

GOOD_PHOTOS: number

BAD_INDEXES: comma-separated indexes of bad photos (0-based), or NONE

ISSUES:
- issue (only real photo problems, in English)

MISSING:
- what is missing for better analysis (angle, turn phase)
- keep short, max 2 points, only if really missing

DISCIPLINE_MATCH: OK / PARTIAL / WRONG

AGE_MATCH: OK / SUSPECT
"""
    else:
        prompt = f"""
Ты тренер по горным лыжам.

Перед анализом нужно проверить набор фото.

Дисциплина: {discipline}
Категория: {category}

Оцени:

1. Соответствие дисциплине (SL / GS)
2. Достаточность фото (минимум 3 хороших фото)
3. Качество (размытость, дистанция)
4. Видимость техники (ноги, корпус, лыжи)
5. Консистентность (один ли спортсмен)
6. Есть ли выбросы (плохие фото)
7. Соответствие возрасту (примерно)

ВАЖНО:
- НЕ требуй идеальности
- цель: можно ли сделать НАДЕЖНЫЙ анализ

Ответ строго в формате:

STATUS: OK / WARNING / NEED_MORE / REJECT

GOOD_PHOTOS: число

BAD_INDEXES: индексы плохих фото через запятую (нумерация с 0), или NONE

ISSUES:
- пункт (только реальные проблемы с фото)

MISSING:
- чего не хватает для более точного анализа (ракурс, фаза поворота)
- пиши коротко, без объяснений почему это важно
- максимум 2 пункта, только если реально чего-то нет

DISCIPLINE_MATCH: OK / PARTIAL / WRONG

AGE_MATCH: OK / SUSPECT
"""

    _t0 = _time.time()
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=500,
        temperature=0.1,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *[
                    {"type": "image_url", "image_url": {"url": url}}
                    for url in image_urls
                ]
            ]
        }]
    )
    log_openai_usage(user_id, OPENAI_MODEL, response, purpose="quality_check", latency_sec=_time.time() - _t0)

    return response.choices[0].message.content
