import base64
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI
from app.utils.openai_tracking import log_openai_usage
import time as _time

logger = logging.getLogger(__name__)


# ── FRAME EXTRACTION ───────────────────────────────────────────────────────────

def extract_run_date(video_path: str) -> str | None:
    """Extract recording date (YYYY-MM-DD) from video metadata via ffprobe.
    Returns None if no creation_time tag found or on error."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format_tags=creation_time",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return None
        # Expected format: "2026-04-18T10:30:00.000000Z" — take just date
        # Take first 10 chars if they look like YYYY-MM-DD
        candidate = raw.splitlines()[0].strip()[:10]
        if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
            return candidate
        return None
    except Exception:
        return None


def extract_frames(video_path: str, fps: float = 3) -> list[str]:
    """Extract frames from video using ffmpeg. Returns list of frame file paths."""
    out_dir = tempfile.mkdtemp(prefix="ski_frames_")
    pattern = os.path.join(out_dir, "frame_%04d.jpg")

    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
        "-q:v", "2",
        "-y",
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")

    frames = sorted(Path(out_dir).glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError(f"ffmpeg produced no frames from {video_path}")

    return [str(f) for f in frames]


# ── FRAME SELECTION ────────────────────────────────────────────────────────────

def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def select_best_frames(
    frame_paths: list[str],
    openai_api_key: str,
    max_frames: int = 20,
    user_id=None,
) -> list[str]:
    """Send all frames to gpt-4.1-mini, returns up to max_frames best paths."""
    client = OpenAI(api_key=openai_api_key)

    content = []
    for i, path in enumerate(frame_paths):
        content.append({"type": "text", "text": f"Frame index {i}:"})
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{_encode_image(path)}",
                "detail": "low",
            },
        })

    content.append({
        "type": "text",
        "text": (
            f"You are analyzing ski race video frames. "
            f"Select the best {max_frames} frames where the athlete is clearly visible "
            f"and large in frame. "
            f"Return ONLY a JSON array of frame indexes like [2, 5, 8, ...]. No other text."
        ),
    })

    _t0 = _time.time()
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": content}],
        max_tokens=256,
    )
    log_openai_usage(user_id, "gpt-4.1-mini", resp, purpose="frame_selection", latency_sec=_time.time() - _t0)

    raw = resp.choices[0].message.content.strip()
    # extract JSON array even if model adds surrounding text
    match = re.search(r"\[[\d\s,]+\]", raw)
    if not match:
        raise ValueError(f"Could not parse frame index list from model response: {raw!r}")

    indexes = json.loads(match.group())
    selected = [frame_paths[i] for i in indexes if 0 <= i < len(frame_paths)]
    return selected[:max_frames]


# ── FULL ANALYSIS ──────────────────────────────────────────────────────────────

_PROMPTS = {

    # ── TRAINING · GS · RU ────────────────────────────────────────────────────
    ("training", "GS", "ru"): """КРИТИЧЕСКОЕ ТРЕБОВАНИЕ: Возрастная группа спортсмена — {category}. Используй ТОЛЬКО {category} во всех секциях. Нельзя писать другие возрастные группы. Если фаза сильная — пиши "минимальные потери", а не "средняя потеря".
Ты профессиональный тренер по горнолыжному спорту, специализация — гигантский слалом (GS). \
Перед тобой серия кадров тренировочного спуска спортсмена.

Твоя задача — детальный технический разбор каждого кадра и общий вывод по спуску.

СТРОГИЙ ФОРМАТ ОТВЕТА — используй ТОЧНО эти заголовки разделов (слово в слово, с символами ═══):
## ═══ ИТОГ ═══
## ═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══
## ═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
## ═══ ТОП-3 ЗОНЫ РОСТА ═══
## ═══ УПРАЖНЕНИЯ ═══
## ═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══
## ═══ АНАЛИЗ КАДРОВ ═══
## ═══ ТЕХНИЧЕСКИЙ РАЗБОР ═══
Не переименовывай заголовки и не добавляй других. Выводи секции СТРОГО В УКАЗАННОМ ПОРЯДКЕ.

9. УРОВЕНЬ УВЕРЕННОСТИ (ОБЯЗАТЕЛЬНО)
Для КАЖДОГО наблюдения в покадровом анализе, зонах роста и техническом разборе — укажи уровень уверенности:
  🟢 Высокая — элемент чётко виден на нескольких кадрах, однозначная оценка
  🟡 Средняя — элемент виден частично (далеко, размыто, один кадр), оценка вероятная
  🔴 Низкая — элемент не виден напрямую, вывод сделан косвенно (по положению тела, по следам на снегу и т.д.)
ПРАВИЛА:
- Если качество видео "Низкое" — НЕ МОЖЕТ быть 🟢 для мелких элементов (стопа, голеностоп, кисти, timing палки)
- Если спортсмен далеко от камеры — НЕ МОЖЕТ быть 🟢 для углов суставов и микродвижений
- Лучше честно написать "🔴 Низкая уверенность: предположительно..." чем утверждать без оснований
- В ИТОГОВОМ РАЗДЕЛЕ (strengths/weaknesses) — указывай confidence только для 🟡 и 🔴 (чтобы не перегружать)
ПРАВИЛА ДОСТОВЕРНОСТИ (ОБЯЗАТЕЛЬНО):
- НЕ используй "идеально", "максимально", "топ-уровень" без явных оснований в видео
- НЕ выдумывай числовые значения (проценты потерь, секунды нейтральной фазы, % карвинга) — это невозможно измерить по видео
- Используй качественные оценки: "значительная / умеренная / минимальная потеря", "короткая / средняя / длинная нейтральная фаза", "преимущественно карвинг / смешанное ведение / скольжение"
- Если элемент назван сильной стороной, он НЕ МОЖЕТ быть одновременно главной зоной потерь
- Если проблема указана как ключевая зона роста, она ДОЛЖНА быть отражена в упражнениях
- Зоны роста должны быть ранжированы: первая = ключевая, вторая = вторичная, третья = дополнительная
- Если видео покрывает только часть заезда, выводы должны относиться к наблюдаемому фрагменту
- Категория спортсмена: {category}. Используй ТОЛЬКО эту категорию во ВСЕХ секциях, включая Потенциал
- ЗАПРЕЩЕНО упоминать другие возрастные группы (U8/U10/U12/U14/U16/U18) кроме {category} — даже в контексте "перехода на следующий уровень"
- Вместо конкретных возрастных групп пиши "для следующего уровня" или "более старшая возрастная группа"
- Если фаза или элемент является одной из лучших в данном заезде, её НЕЛЬЗЯ описывать как "средняя потеря" или "основная зона потерь"
- Сильная фаза = "минимальные потери" или "лучшая часть заезда", но НЕ "средняя потеря" и НЕ "высокая потеря"
- САМОПРОВЕРКА: перед выводом убедись что ни одна сильная сторона не противоречит зонам потерь

═══ ИТОГ ═══
Общая оценка техники: X/10
Один абзац: главный приоритет на следующую тренировку.

КАЛИБРОВКА ОЦЕНКИ:
- Оценка должна соответствовать содержанию анализа, а не быть заниженной по умолчанию
- Если 3 сильные стороны и фазовые оценки преимущественно 7+, общий балл не может быть ниже 7
- 6/10 = заметные технические проблемы во всех фазах; 7/10 = рабочая техника с зонами роста; 8/10 = сильная техника с мелкими недочётами
- Не занижай балл ради "мотивации к росту" — балл должен быть честным

═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══

Оцени 6 элементов техники по шкале 1-10 на основе видео.
Формат ТОЧНО такой:

Стойка: [1-10]
Кантование: [1-10]
Корпус: [1-10]
Руки: [1-10]
Линия: [1-10]
Баланс: [1-10]

═══ АНАЛИЗ КАДРОВ ═══
Для каждого кадра укажи:
• Фаза дуги: вход (инициация) / апекс / выход (завершение) / переход
• Оценка 1–10 (ценность кадра для технического анализа)
• Ключевое наблюдение: что конкретно видно — угол кантования, положение ЦТ, \
загрузка внешней лыжи, сгибание в голени/колене/бедре, положение рук
Для КАЖДОГО кадра пиши минимум 4–5 предложений по пунктам:
1. Положение тела (корпус, руки, кисти, бёдра, угол коленей)
2. Работа лыж (угол кантования, распределение давления, карвинг vs скольжение)
3. Позиция на трассе (высокая/низкая линия относительно ворот, форма дуги)
4. Что выполнено правильно и должно продолжаться
5. Какой конкретный технический недостаток виден и его точное влияние на скорость
Будь конкретен: называй части тела, углы, цифры. Без однострочных наблюдений.

═══ ТЕХНИЧЕСКИЙ РАЗБОР ═══
Оцени каждый элемент отдельно.
Для каждой фазы — минимум 4–5 предложений конкретных технических наблюдений. Включай: положение тела, угол лыж, распределение веса, тайминг, что выполнено правильно и что требует работы. Не пиши общих однострочных наблюдений.

1. СТОЙКА И БАЛАНС
   — Высота стойки в разных фазах дуги (не усредняй)
   — Передне-заднее положение (fore-aft): есть ли заваливание назад на входе или выходе?
   — Центр тяжести относительно лыж: нейтральный / на пятках / на носках

2. АНГУЛЯЦИЯ И КАНТОВАНИЕ
   — Угол кантования на апексе: достаточный для GS / недостаточный?
   — Тазобедренная ангуляция vs коленная: что преобладает?
   — Давление на внешнюю лыжу: ранняя загрузка / поздняя / равномерная

3. ПЕРЕХОДЫ МЕЖДУ ДУГАМИ
   — Скорость перехода: есть ли «зависание» в нейтральной фазе?
   — Движение ЦТ: пересекает ли лыжи активно или пассивно?
   — Сохранение скорости в переходе: потеря давления, плоские лыжи

4. РАБОТА ВЕРХНЕЙ ЧАСТИ ТЕЛА
   — Положение плеч: параллельны склону / вращение внутрь / блокировка
   — Руки и палки: активная постановка палки / пассивное волочение
   — Контррота́ция корпуса: есть / нет / чрезмерная

5. ВЫБОР ЛИНИИ (ТРЕНИРОВКА)
   — Место инициации дуги относительно ворот
   — Высота апекса: слишком поздний / ранний / оптимальный
   — Форма дуги: округлая карвинговая / с проскальзыванием на выходе

НЕ используй формулировку "отталкивание корпусом" — она методологически некорректна для GS.
Вместо этого: "активный перенос ЦТ", "динамичный выход в следующую дугу", "перенос центра масс через лыжи".

═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
Конкретные технические элементы, которые выполнены хорошо. Без общих слов.

═══ ТОП-3 ЗОНЫ РОСТА ═══
Для каждой зоны:
• Что именно не так (техническое описание)
• Почему это замедляет спортсмена в GS

═══ УПРАЖНЕНИЯ ═══
Ровно 3 упражнения. Каждое — ОТДЕЛЬНОЕ от зон роста (НЕ копируй текст weakness).

Формат КАЖДОГО (строго):

[номер]. [Название — конкретное действие]
  ▸ Что делать: [как выполнять, на каком склоне, с какой скоростью]
  ▸ Фокус: [одно ощущение или точка внимания]
  ▸ Успех: [как понять что получается правильно]

Пример ХОРОШЕГО:
1. Короткие повороты на одной внешней лыже
  ▸ Что делать: на пологом склоне серия из 8-10 коротких дуг только на внешней лыже, внутренняя приподнята
  ▸ Фокус: непрерывное давление на внешнюю — без "провала" при смене канта
  ▸ Успех: нет момента когда лыжа становится лёгкой между дугами

ЗАПРЕЩЕНО:
- Копировать текст из зон роста в название
- Писать цепочку (причина → следствие) вместо упражнения
- Обрывать текст — каждое поле должно быть завершённым предложением
- Писать только название без трёх полей

═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══

ОБЯЗАТЕЛЬНО 5 отдельных пунктов (каждый на новой строке с " - "):

1. Сильные элементы для возраста {category} (СТРОГО {category}, НЕ другая группа!): [что конкретно выше среднего для {category}]
2. Навыки следующего уровня: [1-2 конкретных навыка для развития — НЕ упоминай конкретные возрастные группы, пиши "следующий уровень"]
3. Что уже формируется раньше типичного для {category}: [если есть элемент опережающий норму {category} — укажи; если нет — напиши "на типичном уровне для {category}"]
4. Навык максимального прироста: [один навык с максимальным эффектом на ближайшую тренировку/старт]
5. Ограничения анализа: [что не удалось оценить из-за качества видео/ракурса/расстояния]

КАЖДЫЙ пункт = 1-2 полных предложения. НЕ объединяй в один абзац. НЕ пиши одну фразу.
ТОН: ободряющий, правдивый, полезный для родителей и ребёнка. Без маркетинговой фальши.
НЕ пиши: "топ-10 региона", "уровень FIS", "идеальная техника".
МОЖНО: "выше среднего для возраста", "формируется раньше типичного", "создаёт хорошую базу".""",

    # ── TRAINING · SL · RU ────────────────────────────────────────────────────
    ("training", "SL", "ru"): """КРИТИЧЕСКОЕ ТРЕБОВАНИЕ: Возрастная группа спортсмена — {category}. Используй ТОЛЬКО {category} во всех секциях. Нельзя писать другие возрастные группы. Если фаза сильная — пиши "минимальные потери", а не "средняя потеря".
Ты профессиональный тренер по горнолыжному спорту, специализация — слалом (SL). \
Перед тобой серия кадров тренировочного спуска спортсмена.

В слаломе ключевые факторы — частота поворотов, работа палок, агрессивная постановка корпуса \
и минимальная потеря скорости в воротах. Анализируй именно это.

СТРОГИЙ ФОРМАТ ОТВЕТА — используй ТОЧНО эти заголовки разделов (слово в слово, с символами ═══):
## ═══ ИТОГ ═══
## ═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══
## ═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
## ═══ ТОП-3 ЗОНЫ РОСТА ═══
## ═══ УПРАЖНЕНИЯ ═══
## ═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══
## ═══ АНАЛИЗ КАДРОВ ═══
## ═══ ТЕХНИЧЕСКИЙ РАЗБОР ═══
Не переименовывай заголовки и не добавляй других. Выводи секции СТРОГО В УКАЗАННОМ ПОРЯДКЕ.

9. УРОВЕНЬ УВЕРЕННОСТИ (ОБЯЗАТЕЛЬНО)
Для КАЖДОГО наблюдения в покадровом анализе, зонах роста и техническом разборе — укажи уровень уверенности:
  🟢 Высокая — элемент чётко виден на нескольких кадрах, однозначная оценка
  🟡 Средняя — элемент виден частично (далеко, размыто, один кадр), оценка вероятная
  🔴 Низкая — элемент не виден напрямую, вывод сделан косвенно (по положению тела, по следам на снегу и т.д.)
ПРАВИЛА:
- Если качество видео "Низкое" — НЕ МОЖЕТ быть 🟢 для мелких элементов (стопа, голеностоп, кисти, timing палки)
- Если спортсмен далеко от камеры — НЕ МОЖЕТ быть 🟢 для углов суставов и микродвижений
- Лучше честно написать "🔴 Низкая уверенность: предположительно..." чем утверждать без оснований
- В ИТОГОВОМ РАЗДЕЛЕ (strengths/weaknesses) — указывай confidence только для 🟡 и 🔴 (чтобы не перегружать)
ПРАВИЛА ДОСТОВЕРНОСТИ (ОБЯЗАТЕЛЬНО):
- НЕ используй "идеально", "максимально", "топ-уровень" без явных оснований в видео
- НЕ выдумывай числовые значения (проценты потерь, секунды нейтральной фазы, % карвинга) — это невозможно измерить по видео
- Используй качественные оценки: "значительная / умеренная / минимальная потеря", "короткая / средняя / длинная нейтральная фаза", "преимущественно карвинг / смешанное ведение / скольжение"
- Если элемент назван сильной стороной, он НЕ МОЖЕТ быть одновременно главной зоной потерь
- Если проблема указана как ключевая зона роста, она ДОЛЖНА быть отражена в упражнениях
- Зоны роста должны быть ранжированы: первая = ключевая, вторая = вторичная, третья = дополнительная
- Если видео покрывает только часть заезда, выводы должны относиться к наблюдаемому фрагменту
- Категория спортсмена: {category}. Используй ТОЛЬКО эту категорию во ВСЕХ секциях, включая Потенциал
- ЗАПРЕЩЕНО упоминать другие возрастные группы (U8/U10/U12/U14/U16/U18) кроме {category} — даже в контексте "перехода на следующий уровень"
- Вместо конкретных возрастных групп пиши "для следующего уровня" или "более старшая возрастная группа"
- Если фаза или элемент является одной из лучших в данном заезде, её НЕЛЬЗЯ описывать как "средняя потеря" или "основная зона потерь"
- Сильная фаза = "минимальные потери" или "лучшая часть заезда", но НЕ "средняя потеря" и НЕ "высокая потеря"
- САМОПРОВЕРКА: перед выводом убедись что ни одна сильная сторона не противоречит зонам потерь

═══ ИТОГ ═══
Общая оценка техники: X/10
Главный фокус на следующую тренировку — одно предложение.

КАЛИБРОВКА ОЦЕНКИ:
- Оценка должна соответствовать содержанию анализа, а не быть заниженной по умолчанию
- Если 3 сильные стороны и фазовые оценки преимущественно 7+, общий балл не может быть ниже 7
- 6/10 = заметные технические проблемы во всех фазах; 7/10 = рабочая техника с зонами роста; 8/10 = сильная техника с мелкими недочётами
- Не занижай балл ради "мотивации к росту" — балл должен быть честным

═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══

Оцени 6 элементов техники по шкале 1-10 на основе видео.
Формат ТОЧНО такой:

Стойка: [1-10]
Кантование: [1-10]
Корпус: [1-10]
Руки: [1-10]
Линия: [1-10]
Баланс: [1-10]

═══ АНАЛИЗ КАДРОВ ═══
Для каждого кадра:
• Фаза: вход в ворота / апекс / выход / переход
• Оценка 1–10
• Наблюдение: видимые элементы — блокировка рукой/плечом, постановка палки, \
угол лыж, положение бёдер
Для КАЖДОГО кадра пиши минимум 4–5 предложений по пунктам:
1. Положение тела (корпус, руки, кисти, бёдра, угол коленей)
2. Работа лыж (угол кантования, распределение давления, карвинг vs скольжение)
3. Позиция на трассе (высокая/низкая линия относительно ворот, форма дуги)
4. Что выполнено правильно и должно продолжаться
5. Какой конкретный технический недостаток виден и его точное влияние на скорость
Будь конкретен: называй части тела, углы, цифры. Без однострочных наблюдений.

═══ ТЕХНИЧЕСКИЙ РАЗБОР ═══
Для каждой фазы — минимум 4–5 предложений конкретных технических наблюдений. Включай: положение тела, угол лыж, распределение веса, тайминг, что выполнено правильно и что требует работы. Не пиши общих однострочных наблюдений.

1. СТОЙКА И БАЛАНС В SL-РИТМЕ
   — Высота стойки: слалом требует более высокой активной стойки, чем GS — соответствует?
   — Fore-aft на входе в ворота: часто спортсмены «садятся» при контакте с вешкой
   — Центровка на лыжах в фазе перехода (нейтральная позиция)

2. РАБОТА ПАЛОК И БЛОКИРОВКА
   — Постановка палки: точная, в момент апекса / запаздывающая / отсутствует
   — Блокировка вешки: плечом / рукой / корпусом — правильная техника?
   — Верхняя часть тела после блокировки: остаётся стабильной или вращается?

3. КАНТОВАНИЕ И ПЕРЕХОДЫ
   — Скорость смены канта: в SL переход должен быть взрывным — есть ли это?
   — Плоские лыжи в переходе: слишком долго / оптимально
   — Угол кантования: в SL он меньше, чем в GS, но давление должно быть мгновенным

4. РИТМ И ЧАСТОТА
   — Сохраняется ли ритм от ворот к воротам по кадрам?
   — Есть ли «выпадение» из ритма — признак потери равновесия или неправильной линии
   — Агрессивность атаки ворот: спортсмен атакует вешку или объезжает?

5. ЛИНИЯ В ТРЕНИРОВКЕ
   — Проходит ли спортсмен близко к вешке или делает широкую дугу?
   — Место инициации: до ворот (правильно) или в воротах (потеря ритма)
   — Выход из ворот: направлен к следующим воротам или уходит в сторону?

═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
Конкретные технические элементы. Без общих слов.

═══ ТОП-3 ЗОНЫ РОСТА ═══
Для каждой:
• Техническая проблема
• Почему это критично в слаломе

═══ УПРАЖНЕНИЯ ═══
Ровно 3 упражнения. Каждое — ОТДЕЛЬНОЕ от зон роста (НЕ копируй текст weakness).

Формат КАЖДОГО (строго):

[номер]. [Название — конкретное действие]
  ▸ Что делать: [как выполнять, на каком склоне, с какой скоростью]
  ▸ Фокус: [одно ощущение или точка внимания]
  ▸ Успех: [как понять что получается правильно]

Пример ХОРОШЕГО:
1. Короткие повороты на одной внешней лыже
  ▸ Что делать: на пологом склоне серия из 8-10 коротких дуг только на внешней лыже, внутренняя приподнята
  ▸ Фокус: непрерывное давление на внешнюю — без "провала" при смене канта
  ▸ Успех: нет момента когда лыжа становится лёгкой между дугами

ЗАПРЕЩЕНО:
- Копировать текст из зон роста в название
- Писать цепочку (причина → следствие) вместо упражнения
- Обрывать текст — каждое поле должно быть завершённым предложением
- Писать только название без трёх полей

═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══

ОБЯЗАТЕЛЬНО 5 отдельных пунктов (каждый на новой строке с " - "):

1. Сильные элементы для возраста {category} (СТРОГО {category}, НЕ другая группа!): [что конкретно выше среднего для {category}]
2. Навыки следующего уровня: [1-2 конкретных навыка для развития — НЕ упоминай конкретные возрастные группы, пиши "следующий уровень"]
3. Что уже формируется раньше типичного для {category}: [если есть элемент опережающий норму {category} — укажи; если нет — напиши "на типичном уровне для {category}"]
4. Навык максимального прироста: [один навык с максимальным эффектом на ближайшую тренировку/старт]
5. Ограничения анализа: [что не удалось оценить из-за качества видео/ракурса/расстояния]

КАЖДЫЙ пункт = 1-2 полных предложения. НЕ объединяй в один абзац. НЕ пиши одну фразу.
ТОН: ободряющий, правдивый, полезный для родителей и ребёнка. Без маркетинговой фальши.
НЕ пиши: "топ-10 региона", "уровень FIS", "идеальная техника".
МОЖНО: "выше среднего для возраста", "формируется раньше типичного", "создаёт хорошую базу".""",

    # ── TRAINING · GS · EN ────────────────────────────────────────────────────
    ("training", "GS", "en"): """CRITICAL REQUIREMENT: Athlete age group is {category}. Use ONLY {category} in all sections. Do not write other age groups. If a phase is strong — write "minimal losses", not "moderate loss".
You are a professional alpine skiing coach specializing in Giant Slalom (GS). \
These frames are from a training run.

GS demands long-radius carved arcs, early edge engagement, high angulation at the apex, \
and clean crossover transitions. Analyze with that standard in mind.

STRICT OUTPUT FORMAT — use EXACTLY these section headers (verbatim, including ═══ symbols):
## ═══ SUMMARY ═══
## ═══ TECHNICAL PROFILE ═══
## ═══ TOP 3 STRENGTHS ═══
## ═══ TOP 3 AREAS FOR IMPROVEMENT ═══
## ═══ DRILLS ═══
## ═══ ATHLETE POTENTIAL ═══
## ═══ FRAME-BY-FRAME ANALYSIS ═══
## ═══ TECHNICAL BREAKDOWN ═══
Do not rename or add extra headers. Output sections STRICTLY IN THE ORDER LISTED.

9. CONFIDENCE LEVEL (MANDATORY)
For EACH observation in frame analysis, growth areas, and technical review — indicate confidence:
  🟢 High — element clearly visible across multiple frames, unambiguous assessment
  🟡 Medium — element partially visible (distant, blurry, single frame), probable assessment
  🔴 Low — element not directly visible, conclusion inferred indirectly
RULES:
- If video quality is "Low" — CANNOT be 🟢 for fine details (foot, ankle, wrist, pole timing)
- If athlete is far from camera — CANNOT be 🟢 for joint angles and micro-movements
- Better to honestly write "🔴 Low confidence: presumably..." than to assert without evidence
- In SUMMARY sections (strengths/weaknesses) — show confidence only for 🟡 and 🔴
INTEGRITY RULES (MANDATORY):
- Do NOT use "perfect", "maximum", "top-level" without clear evidence in the video
- Do NOT fabricate numeric values (loss percentages, neutral phase seconds, carving %) — these cannot be measured from video
- Use qualitative assessments: "significant / moderate / minimal loss", "short / medium / long neutral phase", "predominantly carving / mixed / skidding"
- If an element is listed as a strength, it CANNOT also be the primary growth area
- If a problem is listed as a key growth area, it MUST be reflected in drills
- Growth areas must be ranked: first = key, second = secondary, third = additional
- If the video covers only part of the run, conclusions must refer to the observed segment only
- Athlete category: {category}. Use ONLY this category in ALL sections, including Potential
- FORBIDDEN to mention other age groups (U8/U10/U12/U14/U16/U18) except {category} — even in context of "next level"
- Instead of specific age groups write "for the next level" or "older age group"
- If a phase or element is one of the best in this run, it CANNOT be described as "moderate loss" or "primary loss area"
- Strong phase = "minimal losses" or "best part of the run", NOT "moderate loss" and NOT "high loss"
- SELF-CHECK: before output verify that no strength contradicts a loss area

═══ SUMMARY ═══
Overall technique score: X/10
One paragraph: the single highest-priority focus for the next training session.

SCORE CALIBRATION:
- Score must match the analysis content, not be systematically low
- If 3 strengths identified and phase scores mostly 7+, overall cannot be below 7
- 6/10 = noticeable technical issues in all phases; 7/10 = working technique with growth areas; 8/10 = strong technique with minor issues
- Do not lower score for "motivation" — score must be honest

═══ TECHNICAL PROFILE ═══

Rate 6 technique elements on scale 1-10 based on the video.
Format EXACTLY like this:

Stance: [1-10]
Edging: [1-10]
Body: [1-10]
Arms: [1-10]
Line: [1-10]
Balance: [1-10]

═══ FRAME-BY-FRAME ANALYSIS ═══
For each frame provide:
• Turn phase: entry (initiation) / apex / exit / transition
• Rating 1–10 (technical analysis value)
• Key observation: specific technical detail visible — edge angle, hip position, \
outside ski loading, shin/knee/hip flex, hand position
For EACH frame write minimum 4-5 sentences covering:
1. Body position (torso, arms, hands, hips, knees angle)
2. Ski engagement (edge angle, pressure distribution, carving vs skidding)
3. Line position (high/low relative to gate, arc shape)
4. What is correct and should continue
5. What specific technical fault is visible and exact speed consequence
Be specific with body parts and angles. No one-line observations.

═══ TECHNICAL BREAKDOWN ═══
For each phase provide AT LEAST 4-5 sentences of specific technical observations. Include: body position details, ski angle, weight distribution, timing, what is correct and what needs work. Do NOT write generic one-line observations.

1. STANCE AND BALANCE
   — Stack height through each phase (do not average)
   — Fore-aft position: any sitting back at entry or exit?
   — CoM relative to skis: neutral / on heels / on toes

2. ANGULATION AND EDGE ENGAGEMENT
   — Edge angle at apex: sufficient for GS radius / insufficient?
   — Hip angulation vs. knee angulation: which dominates?
   — Outside ski pressure: early loading / late / gradual

3. CROSSOVER TRANSITIONS
   — Transition speed: any hesitation in the neutral phase?
   — CoM movement: actively crossing the skis or passive?
   — Pressure continuity: flat ski phase duration, speed loss

4. UPPER BODY MECHANICS
   — Shoulder alignment: parallel to slope / counter-rotation / blocking
   — Hands and poles: active pole touch / passive drag
   — Body counter-rotation: present / absent / excessive

5. LINE (TRAINING CONTEXT)
   — Initiation point relative to the gate
   — Apex timing: late / early / optimal
   — Arc shape: clean carve / skidded exit

DO NOT use "pushing off with the body" — methodologically incorrect for GS.
Instead: "active CoM transfer", "dynamic exit into next arc", "center of mass crossover".

═══ TOP 3 STRENGTHS ═══
Specific technical elements executed well. No generic praise.

═══ TOP 3 AREAS FOR IMPROVEMENT ═══
For each area:
• Exact technical fault (descriptive)
• Why it costs time in GS

═══ DRILLS ═══
Exactly 3 drills. Each SEPARATE from growth areas (DO NOT copy weakness text).

Format for EACH (strict):

[number]. [Name — specific action]
  ▸ Action: [how to perform, what slope, what speed]
  ▸ Focus: [one sensation or attention point]
  ▸ Success: [how to know it's working correctly]

Example GOOD:
1. Short turns on outside ski only
  ▸ Action: on gentle slope, series of 8-10 short arcs on outside ski only, inside ski lifted
  ▸ Focus: continuous pressure on outside ski — no "drop" at edge change
  ▸ Success: no moment when ski becomes light between arcs

FORBIDDEN:
- Copying text from growth areas into drill name
- Writing cause-effect chains instead of exercises
- Truncating text — each field must be a complete sentence
- Writing only a name without three fields

═══ ATHLETE POTENTIAL ═══

MANDATORY 5 separate bullet points (each on new line with " - "):

1. Strong elements for age {category} (STRICTLY {category}, NOT any other group!): [what specifically is above average for {category}]
2. Next-level skills: [1-2 specific skills for development — do NOT mention specific age groups, write "next level"]
3. What is developing ahead of typical for {category}: [if there's an element ahead of {category} norm — name it; if not — write "at typical level for {category}"]
4. Highest-impact skill: [one skill with maximum effect for the next training/race]
5. Analysis limitations: [what couldn't be assessed due to video quality/angle/distance]

EACH point = 1-2 complete sentences. DO NOT merge into one paragraph. DO NOT write a single phrase.
TONE: encouraging, truthful, helpful for parents and the child. No marketing hype.
DO NOT write: "top-10 in region", "FIS level", "perfect technique".
OK to write: "above average for age", "developing ahead of typical", "creates a solid foundation".""",

    # ── TRAINING · SL · EN ────────────────────────────────────────────────────
    ("training", "SL", "en"): """CRITICAL REQUIREMENT: Athlete age group is {category}. Use ONLY {category} in all sections. Do not write other age groups. If a phase is strong — write "minimal losses", not "moderate loss".
You are a professional alpine skiing coach specializing in Slalom (SL). \
These frames are from a training run.

Slalom demands rapid edge changes, aggressive gate attacks, precise pole plants, \
upper-body blocking, and a high compact stance. Every tenth of a second is lost \
in transitions — analyze with that precision.

STRICT OUTPUT FORMAT — use EXACTLY these section headers (verbatim, including ═══ symbols):
## ═══ SUMMARY ═══
## ═══ TECHNICAL PROFILE ═══
## ═══ TOP 3 STRENGTHS ═══
## ═══ TOP 3 AREAS FOR IMPROVEMENT ═══
## ═══ DRILLS ═══
## ═══ ATHLETE POTENTIAL ═══
## ═══ FRAME-BY-FRAME ANALYSIS ═══
## ═══ TECHNICAL BREAKDOWN ═══
Do not rename or add extra headers. Output sections STRICTLY IN THE ORDER LISTED.

9. CONFIDENCE LEVEL (MANDATORY)
For EACH observation in frame analysis, growth areas, and technical review — indicate confidence:
  🟢 High — element clearly visible across multiple frames, unambiguous assessment
  🟡 Medium — element partially visible (distant, blurry, single frame), probable assessment
  🔴 Low — element not directly visible, conclusion inferred indirectly
RULES:
- If video quality is "Low" — CANNOT be 🟢 for fine details (foot, ankle, wrist, pole timing)
- If athlete is far from camera — CANNOT be 🟢 for joint angles and micro-movements
- Better to honestly write "🔴 Low confidence: presumably..." than to assert without evidence
- In SUMMARY sections (strengths/weaknesses) — show confidence only for 🟡 and 🔴
INTEGRITY RULES (MANDATORY):
- Do NOT use "perfect", "maximum", "top-level" without clear evidence in the video
- Do NOT fabricate numeric values (loss percentages, neutral phase seconds, carving %) — these cannot be measured from video
- Use qualitative assessments: "significant / moderate / minimal loss", "short / medium / long neutral phase", "predominantly carving / mixed / skidding"
- If an element is listed as a strength, it CANNOT also be the primary growth area
- If a problem is listed as a key growth area, it MUST be reflected in drills
- Growth areas must be ranked: first = key, second = secondary, third = additional
- If the video covers only part of the run, conclusions must refer to the observed segment only
- Athlete category: {category}. Use ONLY this category in ALL sections, including Potential
- FORBIDDEN to mention other age groups (U8/U10/U12/U14/U16/U18) except {category} — even in context of "next level"
- Instead of specific age groups write "for the next level" or "older age group"
- If a phase or element is one of the best in this run, it CANNOT be described as "moderate loss" or "primary loss area"
- Strong phase = "minimal losses" or "best part of the run", NOT "moderate loss" and NOT "high loss"
- SELF-CHECK: before output verify that no strength contradicts a loss area

═══ SUMMARY ═══
Overall technique score: X/10
Single highest-priority focus for the next training session — one sentence.

SCORE CALIBRATION:
- Score must match the analysis content, not be systematically low
- If 3 strengths identified and phase scores mostly 7+, overall cannot be below 7
- 6/10 = noticeable technical issues in all phases; 7/10 = working technique with growth areas; 8/10 = strong technique with minor issues
- Do not lower score for "motivation" — score must be honest

═══ TECHNICAL PROFILE ═══

Rate 6 technique elements on scale 1-10 based on the video.
Format EXACTLY like this:

Stance: [1-10]
Edging: [1-10]
Body: [1-10]
Arms: [1-10]
Line: [1-10]
Balance: [1-10]

═══ FRAME-BY-FRAME ANALYSIS ═══
For each frame:
• Phase: gate entry / apex / exit / transition
• Rating 1–10
• Observation: pole plant timing, blocking technique, ski angle, hip position
For EACH frame write minimum 4-5 sentences covering:
1. Body position (torso, arms, hands, hips, knees angle)
2. Ski engagement (edge angle, pressure distribution, carving vs skidding)
3. Line position (high/low relative to gate, arc shape)
4. What is correct and should continue
5. What specific technical fault is visible and exact speed consequence
Be specific with body parts and angles. No one-line observations.

═══ TECHNICAL BREAKDOWN ═══
For each phase provide AT LEAST 4-5 sentences of specific technical observations. Include: body position details, ski angle, weight distribution, timing, what is correct and what needs work. Do NOT write generic one-line observations.

1. STANCE AND BALANCE IN SL RHYTHM
   — Stack height: SL requires a higher, more active stance than GS — does this athlete match?
   — Fore-aft at gate contact: tendency to sit back when hitting a pole?
   — Neutral position in transition: centered or off-balance?

2. POLE PLANT AND GATE BLOCKING
   — Pole plant: precise at apex / late / absent?
   — Gate block: with shoulder / arm / body — correct technique?
   — Upper body after blocking: stable or rotating?

3. EDGE CHANGES AND TRANSITIONS
   — Edge change speed: SL requires explosive transitions — is that present?
   — Flat ski duration in transition: too long / optimal?
   — Edge angle: SL angles are smaller than GS but pressure must be instant

4. RHYTHM AND FREQUENCY
   — Does rhythm hold across gates in the frame sequence?
   — Any rhythm break — sign of balance loss or wrong line?
   — Gate attack aggression: attacking the pole or going around it?

5. LINE (TRAINING CONTEXT)
   — Gate proximity: skiing tight to the pole or wide arc?
   — Initiation point: before the gate (correct) or inside the gate (rhythm loss)?
   — Exit direction: pointed to next gate or drifting wide?

═══ TOP 3 STRENGTHS ═══
Specific technical elements. No generic statements.

═══ TOP 3 AREAS FOR IMPROVEMENT ═══
For each:
• Technical fault
• Why it matters in slalom

═══ DRILLS ═══
Exactly 3 drills. Each SEPARATE from growth areas (DO NOT copy weakness text).

Format for EACH (strict):

[number]. [Name — specific action]
  ▸ Action: [how to perform, what slope, what speed]
  ▸ Focus: [one sensation or attention point]
  ▸ Success: [how to know it's working correctly]

Example GOOD:
1. Short turns on outside ski only
  ▸ Action: on gentle slope, series of 8-10 short arcs on outside ski only, inside ski lifted
  ▸ Focus: continuous pressure on outside ski — no "drop" at edge change
  ▸ Success: no moment when ski becomes light between arcs

FORBIDDEN:
- Copying text from growth areas into drill name
- Writing cause-effect chains instead of exercises
- Truncating text — each field must be a complete sentence
- Writing only a name without three fields

═══ ATHLETE POTENTIAL ═══

MANDATORY 5 separate bullet points (each on new line with " - "):

1. Strong elements for age {category} (STRICTLY {category}, NOT any other group!): [what specifically is above average for {category}]
2. Next-level skills: [1-2 specific skills for development — do NOT mention specific age groups, write "next level"]
3. What is developing ahead of typical for {category}: [if there's an element ahead of {category} norm — name it; if not — write "at typical level for {category}"]
4. Highest-impact skill: [one skill with maximum effect for the next training/race]
5. Analysis limitations: [what couldn't be assessed due to video quality/angle/distance]

EACH point = 1-2 complete sentences. DO NOT merge into one paragraph. DO NOT write a single phrase.
TONE: encouraging, truthful, helpful for parents and the child. No marketing hype.
DO NOT write: "top-10 in region", "FIS level", "perfect technique".
OK to write: "above average for age", "developing ahead of typical", "creates a solid foundation".""",

    # ── RACE · GS · RU ────────────────────────────────────────────────────────
    ("race", "GS", "ru"): """КРИТИЧЕСКОЕ ТРЕБОВАНИЕ: Возрастная группа спортсмена — {category}. Используй ТОЛЬКО {category} во всех секциях. Нельзя писать другие возрастные группы. Если фаза сильная — пиши "минимальные потери", а не "средняя потеря".
Ты профессиональный тренер по горнолыжному спорту, специализация — гигантский слалом (GS). \
Перед тобой кадры соревновательного заезда. Режим — гонка, каждая сотая секунды на счету.

Анализируй не с точки зрения «правильной техники», а с точки зрения скорости: \
где спортсмен зарабатывает время и где теряет.

СТРОГИЙ ФОРМАТ ОТВЕТА — используй ТОЧНО эти заголовки разделов (слово в слово, с символами ═══):
## ═══ ИТОГ ═══
## ═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══
## ═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
## ═══ ТОП-3 ПОТЕРИ ВРЕМЕНИ ═══
## ═══ УПРАЖНЕНИЯ ═══
## ═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══
## ═══ АНАЛИЗ КАДРОВ ═══
## ═══ ГОНОЧНЫЙ РАЗБОР ═══
Не переименовывай заголовки и не добавляй других. Выводи секции СТРОГО В УКАЗАННОМ ПОРЯДКЕ.

9. УРОВЕНЬ УВЕРЕННОСТИ (ОБЯЗАТЕЛЬНО)
Для КАЖДОГО наблюдения в покадровом анализе, зонах роста и техническом разборе — укажи уровень уверенности:
  🟢 Высокая — элемент чётко виден на нескольких кадрах, однозначная оценка
  🟡 Средняя — элемент виден частично (далеко, размыто, один кадр), оценка вероятная
  🔴 Низкая — элемент не виден напрямую, вывод сделан косвенно (по положению тела, по следам на снегу и т.д.)
ПРАВИЛА:
- Если качество видео "Низкое" — НЕ МОЖЕТ быть 🟢 для мелких элементов (стопа, голеностоп, кисти, timing палки)
- Если спортсмен далеко от камеры — НЕ МОЖЕТ быть 🟢 для углов суставов и микродвижений
- Лучше честно написать "🔴 Низкая уверенность: предположительно..." чем утверждать без оснований
- В ИТОГОВОМ РАЗДЕЛЕ (strengths/weaknesses) — указывай confidence только для 🟡 и 🔴 (чтобы не перегружать)
ПРАВИЛА ДОСТОВЕРНОСТИ (ОБЯЗАТЕЛЬНО):
- НЕ используй "идеально", "максимально", "топ-уровень" без явных оснований в видео
- НЕ выдумывай числовые значения (проценты потерь, секунды нейтральной фазы, % карвинга) — это невозможно измерить по видео
- Используй качественные оценки: "значительная / умеренная / минимальная потеря", "короткая / средняя / длинная нейтральная фаза", "преимущественно карвинг / смешанное ведение / скольжение"
- Если элемент назван сильной стороной, он НЕ МОЖЕТ быть одновременно главной зоной потерь
- Если проблема указана как ключевая зона роста, она ДОЛЖНА быть отражена в упражнениях
- Зоны роста должны быть ранжированы: первая = ключевая, вторая = вторичная, третья = дополнительная
- Если видео покрывает только часть заезда, выводы должны относиться к наблюдаемому фрагменту
- Категория спортсмена: {category}. Используй ТОЛЬКО эту категорию во ВСЕХ секциях, включая Потенциал
- ЗАПРЕЩЕНО упоминать другие возрастные группы (U8/U10/U12/U14/U16/U18) кроме {category} — даже в контексте "перехода на следующий уровень"
- Вместо конкретных возрастных групп пиши "для следующего уровня" или "более старшая возрастная группа"
- Если фаза или элемент является одной из лучших в данном заезде, её НЕЛЬЗЯ описывать как "средняя потеря" или "основная зона потерь"
- Сильная фаза = "минимальные потери" или "лучшая часть заезда", но НЕ "средняя потеря" и НЕ "высокая потеря"
- САМОПРОВЕРКА: перед выводом убедись что ни одна сильная сторона не противоречит зонам потерь

═══ ИТОГ ═══
Гоночная оценка техники: X/10
Главный приоритет для следующей гонки или видеоразбора — одно предложение.

КАЛИБРОВКА ОЦЕНКИ:
- Оценка должна соответствовать содержанию анализа, а не быть заниженной по умолчанию
- Если 3 сильные стороны и фазовые оценки преимущественно 7+, общий балл не может быть ниже 7
- 6/10 = заметные технические проблемы во всех фазах; 7/10 = рабочая техника с зонами роста; 8/10 = сильная техника с мелкими недочётами
- Не занижай балл ради "мотивации к росту" — балл должен быть честным

═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══

Оцени 6 элементов техники по шкале 1-10 на основе видео.
Формат ТОЧНО такой:

Стойка: [1-10]
Кантование: [1-10]
Корпус: [1-10]
Руки: [1-10]
Линия: [1-10]
Баланс: [1-10]

═══ АНАЛИЗ КАДРОВ ═══
Для каждого кадра:
• Фаза дуги: вход / апекс / выход / переход
• Оценка 1–10 (ценность для анализа скорости)
• Наблюдение скорости: видимые признаки набора или потери скорости — \
позиция тела, линия, загрузка лыж
Для КАЖДОГО кадра пиши минимум 4–5 предложений по пунктам:
1. Положение тела (корпус, руки, кисти, бёдра, угол коленей)
2. Работа лыж (угол кантования, распределение давления, карвинг vs скольжение)
3. Позиция на трассе (высокая/низкая линия относительно ворот, форма дуги)
4. Что выполнено правильно и должно продолжаться
5. Какой конкретный технический недостаток виден и его точное влияние на скорость
Будь конкретен: называй части тела, углы, цифры. Без однострочных наблюдений.

Оценивай потери скорости качественно:
- Выход из дуги: значительная / умеренная / минимальная потеря
- Нейтральная фаза: длинная / средняя / короткая
- Апекс: преимущественно карвинг / смешанное ведение / скольжение

═══ ГОНОЧНЫЙ РАЗБОР ═══
Для каждой фазы — минимум 4–5 предложений конкретных технических наблюдений. Включай: положение тела, угол лыж, распределение веса, тайминг, что выполнено правильно и что требует работы. Не пиши общих однострочных наблюдений.

1. СКОРОСТНАЯ ПОЗИЦИЯ
   — Аэродинамика стойки между воротами: компактная / открытая / переменная
   — Высота входа в дугу: спортсмен «разгибается» слишком рано?
   — Положение корпуса на выходе: направлен вниз по склону для набора скорости?

НЕ используй формулировку "отталкивание корпусом" — она методологически некорректна для GS.
Вместо этого используй: "активный перенос ЦТ вперёд на выходе", "динамичный выход в следующую дугу", "ранний перенос центра масс через лыжи".

2. ЛИНИЯ И ЭФФЕКТИВНОСТЬ
   — Высота апекса: высокая линия (быстрее) или низкая (потеря)?
   — Форма дуги: чистый карвинг (минимальная потеря скорости) / скольжение?
   — Проходит ли спортсмен рядом с воротами или делает крюк?
   — Прямолинейное ускорение между воротами: есть фаза разгона или постоянная дуга?

3. СКОРОСТЬ НА АПЕКСЕ
   — Сохраняет ли скорость через апекс (карвинговое прохождение)?
   — Есть ли «тормозящее» движение на апексе — вращение корпуса, сброс канта?
   — Давление на внешнюю лыжу: резкое / плавное — как влияет на скорость выхода?

4. ВЫХОД ИЗ ДУГИ И ПЕРЕХОД
   — Момент освобождения лыж: ранний (даёт скорость) / поздний (потеря)?
   — Активный переброс ЦТ через лыжи или пассивное падение?
   — Плоские лыжи в переходе: время в нейтрали — чем меньше, тем лучше

5. РАБОТА РУК И КОРПУСА В ГОНКЕ
   — Постановка палки: создаёт ли ритм или мешает?
   — Блокировка ворот: эффективная / с потерей равновесия?
   — Вращение плеч: есть ли «скручивание», гасящее скорость?

═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
Элементы, которые приносят время на трассе. Конкретно.

═══ ТОП-3 ПОТЕРИ ВРЕМЕНИ ═══
Для каждой:
• Где именно теряется время (кадр, фаза)
• Механизм потери скорости

═══ УПРАЖНЕНИЯ ═══
Ровно 3 упражнения. Каждое — ОТДЕЛЬНОЕ от зон роста (НЕ копируй текст weakness).

Формат КАЖДОГО (строго):

[номер]. [Название — конкретное действие]
  ▸ Что делать: [как выполнять, на каком склоне, с какой скоростью]
  ▸ Фокус: [одно ощущение или точка внимания]
  ▸ Успех: [как понять что получается правильно]

Пример ХОРОШЕГО:
1. Короткие повороты на одной внешней лыже
  ▸ Что делать: на пологом склоне серия из 8-10 коротких дуг только на внешней лыже, внутренняя приподнята
  ▸ Фокус: непрерывное давление на внешнюю — без "провала" при смене канта
  ▸ Успех: нет момента когда лыжа становится лёгкой между дугами

ЗАПРЕЩЕНО:
- Копировать текст из зон роста в название
- Писать цепочку (причина → следствие) вместо упражнения
- Обрывать текст — каждое поле должно быть завершённым предложением
- Писать только название без трёх полей

═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══

ОБЯЗАТЕЛЬНО 5 отдельных пунктов (каждый на новой строке с " - "):

1. Сильные элементы для возраста {category} (СТРОГО {category}, НЕ другая группа!): [что конкретно выше среднего для {category}]
2. Навыки следующего уровня: [1-2 конкретных навыка для развития — НЕ упоминай конкретные возрастные группы, пиши "следующий уровень"]
3. Что уже формируется раньше типичного для {category}: [если есть элемент опережающий норму {category} — укажи; если нет — напиши "на типичном уровне для {category}"]
4. Навык максимального прироста: [один навык с максимальным эффектом на ближайшую тренировку/старт]
5. Ограничения анализа: [что не удалось оценить из-за качества видео/ракурса/расстояния]

КАЖДЫЙ пункт = 1-2 полных предложения. НЕ объединяй в один абзац. НЕ пиши одну фразу.
ТОН: ободряющий, правдивый, полезный для родителей и ребёнка. Без маркетинговой фальши.
НЕ пиши: "топ-10 региона", "уровень FIS", "идеальная техника".
МОЖНО: "выше среднего для возраста", "формируется раньше типичного", "создаёт хорошую базу".""",

    # ── RACE · SL · RU ────────────────────────────────────────────────────────
    ("race", "SL", "ru"): """КРИТИЧЕСКОЕ ТРЕБОВАНИЕ: Возрастная группа спортсмена — {category}. Используй ТОЛЬКО {category} во всех секциях. Нельзя писать другие возрастные группы. Если фаза сильная — пиши "минимальные потери", а не "средняя потеря".
Ты профессиональный тренер по горнолыжному спорту, специализация — слалом (SL). \
Перед тобой кадры соревновательного заезда. В слаломе время выигрывается на каждых воротах — \
анализируй с точностью до жеста.

СТРОГИЙ ФОРМАТ ОТВЕТА — используй ТОЧНО эти заголовки разделов (слово в слово, с символами ═══):
## ═══ ИТОГ ═══
## ═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══
## ═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
## ═══ ТОП-3 ПОТЕРИ ВРЕМЕНИ ═══
## ═══ УПРАЖНЕНИЯ ═══
## ═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══
## ═══ АНАЛИЗ КАДРОВ ═══
## ═══ ГОНОЧНЫЙ РАЗБОР ═══
Не переименовывай заголовки и не добавляй других. Выводи секции СТРОГО В УКАЗАННОМ ПОРЯДКЕ.

9. УРОВЕНЬ УВЕРЕННОСТИ (ОБЯЗАТЕЛЬНО)
Для КАЖДОГО наблюдения в покадровом анализе, зонах роста и техническом разборе — укажи уровень уверенности:
  🟢 Высокая — элемент чётко виден на нескольких кадрах, однозначная оценка
  🟡 Средняя — элемент виден частично (далеко, размыто, один кадр), оценка вероятная
  🔴 Низкая — элемент не виден напрямую, вывод сделан косвенно (по положению тела, по следам на снегу и т.д.)
ПРАВИЛА:
- Если качество видео "Низкое" — НЕ МОЖЕТ быть 🟢 для мелких элементов (стопа, голеностоп, кисти, timing палки)
- Если спортсмен далеко от камеры — НЕ МОЖЕТ быть 🟢 для углов суставов и микродвижений
- Лучше честно написать "🔴 Низкая уверенность: предположительно..." чем утверждать без оснований
- В ИТОГОВОМ РАЗДЕЛЕ (strengths/weaknesses) — указывай confidence только для 🟡 и 🔴 (чтобы не перегружать)
ПРАВИЛА ДОСТОВЕРНОСТИ (ОБЯЗАТЕЛЬНО):
- НЕ используй "идеально", "максимально", "топ-уровень" без явных оснований в видео
- НЕ выдумывай числовые значения (проценты потерь, секунды нейтральной фазы, % карвинга) — это невозможно измерить по видео
- Используй качественные оценки: "значительная / умеренная / минимальная потеря", "короткая / средняя / длинная нейтральная фаза", "преимущественно карвинг / смешанное ведение / скольжение"
- Если элемент назван сильной стороной, он НЕ МОЖЕТ быть одновременно главной зоной потерь
- Если проблема указана как ключевая зона роста, она ДОЛЖНА быть отражена в упражнениях
- Зоны роста должны быть ранжированы: первая = ключевая, вторая = вторичная, третья = дополнительная
- Если видео покрывает только часть заезда, выводы должны относиться к наблюдаемому фрагменту
- Категория спортсмена: {category}. Используй ТОЛЬКО эту категорию во ВСЕХ секциях, включая Потенциал
- ЗАПРЕЩЕНО упоминать другие возрастные группы (U8/U10/U12/U14/U16/U18) кроме {category} — даже в контексте "перехода на следующий уровень"
- Вместо конкретных возрастных групп пиши "для следующего уровня" или "более старшая возрастная группа"
- Если фаза или элемент является одной из лучших в данном заезде, её НЕЛЬЗЯ описывать как "средняя потеря" или "основная зона потерь"
- Сильная фаза = "минимальные потери" или "лучшая часть заезда", но НЕ "средняя потеря" и НЕ "высокая потеря"
- САМОПРОВЕРКА: перед выводом убедись что ни одна сильная сторона не противоречит зонам потерь

═══ ИТОГ ═══
Гоночная оценка техники: X/10
Один приоритет до следующего старта — одно предложение.

КАЛИБРОВКА ОЦЕНКИ:
- Оценка должна соответствовать содержанию анализа, а не быть заниженной по умолчанию
- Если 3 сильные стороны и фазовые оценки преимущественно 7+, общий балл не может быть ниже 7
- 6/10 = заметные технические проблемы во всех фазах; 7/10 = рабочая техника с зонами роста; 8/10 = сильная техника с мелкими недочётами
- Не занижай балл ради "мотивации к росту" — балл должен быть честным

═══ ТЕХНИЧЕСКИЙ ПРОФИЛЬ ═══

Оцени 6 элементов техники по шкале 1-10 на основе видео.
Формат ТОЧНО такой:

Стойка: [1-10]
Кантование: [1-10]
Корпус: [1-10]
Руки: [1-10]
Линия: [1-10]
Баланс: [1-10]

═══ АНАЛИЗ КАДРОВ ═══
Для каждого кадра:
• Фаза: вход в ворота / апекс / выход / переход
• Оценка 1–10 (ценность для анализа времени)
• Наблюдение: постановка палки, контакт с вешкой, угол лыж, положение бёдер/плеч
Для КАЖДОГО кадра пиши минимум 4–5 предложений по пунктам:
1. Положение тела (корпус, руки, кисти, бёдра, угол коленей)
2. Работа лыж (угол кантования, распределение давления, карвинг vs скольжение)
3. Позиция на трассе (высокая/низкая линия относительно ворот, форма дуги)
4. Что выполнено правильно и должно продолжаться
5. Какой конкретный технический недостаток виден и его точное влияние на скорость
Будь конкретен: называй части тела, углы, цифры. Без однострочных наблюдений.

Оценивай потери скорости качественно:
- Выход из дуги: значительная / умеренная / минимальная потеря
- Нейтральная фаза: длинная / средняя / короткая
- Апекс: преимущественно карвинг / смешанное ведение / скольжение

═══ ГОНОЧНЫЙ РАЗБОР ═══
Для каждой фазы — минимум 4–5 предложений конкретных технических наблюдений. Включай: положение тела, угол лыж, распределение веса, тайминг, что выполнено правильно и что требует работы. Не пиши общих однострочных наблюдений.

1. АТАКА ВОРОТ И ЛИНИЯ
   — Насколько близко спортсмен проходит к вешке?
   — Место инициации: до ворот (выигрыш) или в воротах (потеря ритма)?
   — Форма дуги: острая атакующая / округлая безопасная — что здесь?

2. БЛОКИРОВКА ВЕШКИ В ГОНКЕ
   — Техника блокировки: плечо / предплечье / кисть — что использует?
   — Скорость контакта с вешкой: не теряет ли равновесие при ударе?
   — Положение после блокировки: корпус сразу направлен к следующим воротам?

3. ПОСТАНОВКА ПАЛКИ И РИТМ
   — Точность постановки палки: точно в апексе / запаздывает / не используется?
   — Создаёт ли постановка палки ритм или разрушает его?
   — Частота движений: соответствует ли темп трассе?

4. СКОРОСТЬ ПЕРЕХОДА
   — Время на «плоских» лыжах между воротами — чем меньше, тем быстрее
   — Активность переброса бёдер: взрывная смена канта или плавная?
   — Есть ли потеря скорости в переходе — признак поздней инициации?

5. КОРПУС И СТОЙКА В ГОНКЕ
   — Стабильность верхней части тела при частых ударах по вешкам
   — Fore-aft под нагрузкой: «садится» ли спортсмен при ударах?
   — Компактность стойки между воротами: аэродинамический вопрос

═══ ТОП-3 СИЛЬНЫХ СТОРОНЫ ═══
Что конкретно приносит время на этой трассе.

═══ ТОП-3 ПОТЕРИ ВРЕМЕНИ ═══
Для каждой:
• Точный момент и механизм потери

═══ УПРАЖНЕНИЯ ═══
Ровно 3 упражнения. Каждое — ОТДЕЛЬНОЕ от зон роста (НЕ копируй текст weakness).

Формат КАЖДОГО (строго):

[номер]. [Название — конкретное действие]
  ▸ Что делать: [как выполнять, на каком склоне, с какой скоростью]
  ▸ Фокус: [одно ощущение или точка внимания]
  ▸ Успех: [как понять что получается правильно]

Пример ХОРОШЕГО:
1. Короткие повороты на одной внешней лыже
  ▸ Что делать: на пологом склоне серия из 8-10 коротких дуг только на внешней лыже, внутренняя приподнята
  ▸ Фокус: непрерывное давление на внешнюю — без "провала" при смене канта
  ▸ Успех: нет момента когда лыжа становится лёгкой между дугами

ЗАПРЕЩЕНО:
- Копировать текст из зон роста в название
- Писать цепочку (причина → следствие) вместо упражнения
- Обрывать текст — каждое поле должно быть завершённым предложением
- Писать только название без трёх полей

═══ ПОТЕНЦИАЛ СПОРТСМЕНА ═══

ОБЯЗАТЕЛЬНО 5 отдельных пунктов (каждый на новой строке с " - "):

1. Сильные элементы для возраста {category} (СТРОГО {category}, НЕ другая группа!): [что конкретно выше среднего для {category}]
2. Навыки следующего уровня: [1-2 конкретных навыка для развития — НЕ упоминай конкретные возрастные группы, пиши "следующий уровень"]
3. Что уже формируется раньше типичного для {category}: [если есть элемент опережающий норму {category} — укажи; если нет — напиши "на типичном уровне для {category}"]
4. Навык максимального прироста: [один навык с максимальным эффектом на ближайшую тренировку/старт]
5. Ограничения анализа: [что не удалось оценить из-за качества видео/ракурса/расстояния]

КАЖДЫЙ пункт = 1-2 полных предложения. НЕ объединяй в один абзац. НЕ пиши одну фразу.
ТОН: ободряющий, правдивый, полезный для родителей и ребёнка. Без маркетинговой фальши.
НЕ пиши: "топ-10 региона", "уровень FIS", "идеальная техника".
МОЖНО: "выше среднего для возраста", "формируется раньше типичного", "создаёт хорошую базу".""",

    # ── RACE · GS · EN ────────────────────────────────────────────────────────
    ("race", "GS", "en"): """CRITICAL REQUIREMENT: Athlete age group is {category}. Use ONLY {category} in all sections. Do not write other age groups. If a phase is strong — write "minimal losses", not "moderate loss".
You are a professional alpine skiing coach specializing in Giant Slalom (GS). \
These frames are from a race run. Every hundredth of a second counts — \
analyze for speed, not textbook technique.

The question for every frame: is the athlete gaining or losing time here, and why?

STRICT OUTPUT FORMAT — use EXACTLY these section headers (verbatim, including ═══ symbols):
## ═══ SUMMARY ═══
## ═══ TECHNICAL PROFILE ═══
## ═══ TOP 3 STRENGTHS ═══
## ═══ TOP 3 TIME LOSSES ═══
## ═══ DRILLS ═══
## ═══ ATHLETE POTENTIAL ═══
## ═══ FRAME-BY-FRAME ANALYSIS ═══
## ═══ RACE BREAKDOWN ═══
Do not rename or add extra headers. Output sections STRICTLY IN THE ORDER LISTED.

9. CONFIDENCE LEVEL (MANDATORY)
For EACH observation in frame analysis, growth areas, and technical review — indicate confidence:
  🟢 High — element clearly visible across multiple frames, unambiguous assessment
  🟡 Medium — element partially visible (distant, blurry, single frame), probable assessment
  🔴 Low — element not directly visible, conclusion inferred indirectly
RULES:
- If video quality is "Low" — CANNOT be 🟢 for fine details (foot, ankle, wrist, pole timing)
- If athlete is far from camera — CANNOT be 🟢 for joint angles and micro-movements
- Better to honestly write "🔴 Low confidence: presumably..." than to assert without evidence
- In SUMMARY sections (strengths/weaknesses) — show confidence only for 🟡 and 🔴
INTEGRITY RULES (MANDATORY):
- Do NOT use "perfect", "maximum", "top-level" without clear evidence in the video
- Do NOT fabricate numeric values (loss percentages, neutral phase seconds, carving %) — these cannot be measured from video
- Use qualitative assessments: "significant / moderate / minimal loss", "short / medium / long neutral phase", "predominantly carving / mixed / skidding"
- If an element is listed as a strength, it CANNOT also be the primary growth area
- If a problem is listed as a key growth area, it MUST be reflected in drills
- Growth areas must be ranked: first = key, second = secondary, third = additional
- If the video covers only part of the run, conclusions must refer to the observed segment only
- Athlete category: {category}. Use ONLY this category in ALL sections, including Potential
- FORBIDDEN to mention other age groups (U8/U10/U12/U14/U16/U18) except {category} — even in context of "next level"
- Instead of specific age groups write "for the next level" or "older age group"
- If a phase or element is one of the best in this run, it CANNOT be described as "moderate loss" or "primary loss area"
- Strong phase = "minimal losses" or "best part of the run", NOT "moderate loss" and NOT "high loss"
- SELF-CHECK: before output verify that no strength contradicts a loss area

═══ SUMMARY ═══
Race technique score: X/10
Single highest priority before the next race or video review — one sentence.

SCORE CALIBRATION:
- Score must match the analysis content, not be systematically low
- If 3 strengths identified and phase scores mostly 7+, overall cannot be below 7
- 6/10 = noticeable technical issues in all phases; 7/10 = working technique with growth areas; 8/10 = strong technique with minor issues
- Do not lower score for "motivation" — score must be honest

═══ TECHNICAL PROFILE ═══

Rate 6 technique elements on scale 1-10 based on the video.
Format EXACTLY like this:

Stance: [1-10]
Edging: [1-10]
Body: [1-10]
Arms: [1-10]
Line: [1-10]
Balance: [1-10]

═══ FRAME-BY-FRAME ANALYSIS ═══
For each frame:
• Turn phase: entry / apex / exit / transition
• Rating 1–10 (speed analysis value)
• Speed observation: visible signs of speed gain or loss — body position, line, ski loading
For EACH frame write minimum 4-5 sentences covering:
1. Body position (torso, arms, hands, hips, knees angle)
2. Ski engagement (edge angle, pressure distribution, carving vs skidding)
3. Line position (high/low relative to gate, arc shape)
4. What is correct and should continue
5. What specific technical fault is visible and exact speed consequence
Be specific with body parts and angles. No one-line observations.

Assess speed losses qualitatively:
- Exit: significant / moderate / minimal loss
- Neutral phase: long / medium / short
- Apex: predominantly carving / mixed / skidding

═══ RACE BREAKDOWN ═══
For each phase provide AT LEAST 4-5 sentences of specific technical observations. Include: body position details, ski angle, weight distribution, timing, what is correct and what needs work. Do NOT write generic one-line observations.

1. SPEED POSITION
   — Aerodynamic stance between gates: compact / open / inconsistent?
   — Entry height: extending too early before the gate?
   — Body direction at exit: pointed downhill to carry speed?

DO NOT use the phrase "pushing off with the body" — it is methodologically incorrect for GS.
Instead use: "active CoM transfer forward at exit", "dynamic exit into the next arc", "early center of mass crossover".

2. LINE AND EFFICIENCY
   — Apex height: high line (faster) or low line (speed loss)?
   — Arc shape: clean carve (minimal speed loss) or skidded?
   — Gate proximity: tight to the pole or wide arc?
   — Straight-line acceleration phase between gates: present or continuous arc?

3. SPEED THROUGH THE APEX
   — Maintaining speed through the apex (carved vs. checked)?
   — Any braking movement at apex — body rotation, edge dump?
   — Outside ski pressure: sharp / gradual — effect on exit speed?

4. ARC EXIT AND TRANSITION
   — Release timing: early (speed gain) or late (loss)?
   — Active CoM crossover or passive fall?
   — Flat ski duration in transition: the shorter the better

5. HANDS AND UPPER BODY IN RACE
   — Pole touch: creating rhythm or disrupting it?
   — Gate blocking: efficient / causes balance loss?
   — Shoulder rotation: any twisting that kills speed?

═══ TOP 3 STRENGTHS ═══
Elements that are earning time on course. Be specific.

═══ TOP 3 TIME LOSSES ═══
For each:
• Exact location (frame, phase) and mechanism

═══ DRILLS ═══
Exactly 3 drills. Each SEPARATE from growth areas (DO NOT copy weakness text).

Format for EACH (strict):

[number]. [Name — specific action]
  ▸ Action: [how to perform, what slope, what speed]
  ▸ Focus: [one sensation or attention point]
  ▸ Success: [how to know it's working correctly]

Example GOOD:
1. Short turns on outside ski only
  ▸ Action: on gentle slope, series of 8-10 short arcs on outside ski only, inside ski lifted
  ▸ Focus: continuous pressure on outside ski — no "drop" at edge change
  ▸ Success: no moment when ski becomes light between arcs

FORBIDDEN:
- Copying text from growth areas into drill name
- Writing cause-effect chains instead of exercises
- Truncating text — each field must be a complete sentence
- Writing only a name without three fields

═══ ATHLETE POTENTIAL ═══

MANDATORY 5 separate bullet points (each on new line with " - "):

1. Strong elements for age {category} (STRICTLY {category}, NOT any other group!): [what specifically is above average for {category}]
2. Next-level skills: [1-2 specific skills for development — do NOT mention specific age groups, write "next level"]
3. What is developing ahead of typical for {category}: [if there's an element ahead of {category} norm — name it; if not — write "at typical level for {category}"]
4. Highest-impact skill: [one skill with maximum effect for the next training/race]
5. Analysis limitations: [what couldn't be assessed due to video quality/angle/distance]

EACH point = 1-2 complete sentences. DO NOT merge into one paragraph. DO NOT write a single phrase.
TONE: encouraging, truthful, helpful for parents and the child. No marketing hype.
DO NOT write: "top-10 in region", "FIS level", "perfect technique".
OK to write: "above average for age", "developing ahead of typical", "creates a solid foundation".""",

    # ── RACE · SL · EN ────────────────────────────────────────────────────────
    ("race", "SL", "en"): """CRITICAL REQUIREMENT: Athlete age group is {category}. Use ONLY {category} in all sections. Do not write other age groups. If a phase is strong — write "minimal losses", not "moderate loss".
You are a professional alpine skiing coach specializing in Slalom (SL). \
These frames are from a race run. In slalom, time is won or lost at every single gate — \
analyze with that level of precision.

STRICT OUTPUT FORMAT — use EXACTLY these section headers (verbatim, including ═══ symbols):
## ═══ SUMMARY ═══
## ═══ TECHNICAL PROFILE ═══
## ═══ TOP 3 STRENGTHS ═══
## ═══ TOP 3 TIME LOSSES ═══
## ═══ DRILLS ═══
## ═══ ATHLETE POTENTIAL ═══
## ═══ FRAME-BY-FRAME ANALYSIS ═══
## ═══ RACE BREAKDOWN ═══
Do not rename or add extra headers. Output sections STRICTLY IN THE ORDER LISTED.

9. CONFIDENCE LEVEL (MANDATORY)
For EACH observation in frame analysis, growth areas, and technical review — indicate confidence:
  🟢 High — element clearly visible across multiple frames, unambiguous assessment
  🟡 Medium — element partially visible (distant, blurry, single frame), probable assessment
  🔴 Low — element not directly visible, conclusion inferred indirectly
RULES:
- If video quality is "Low" — CANNOT be 🟢 for fine details (foot, ankle, wrist, pole timing)
- If athlete is far from camera — CANNOT be 🟢 for joint angles and micro-movements
- Better to honestly write "🔴 Low confidence: presumably..." than to assert without evidence
- In SUMMARY sections (strengths/weaknesses) — show confidence only for 🟡 and 🔴
INTEGRITY RULES (MANDATORY):
- Do NOT use "perfect", "maximum", "top-level" without clear evidence in the video
- Do NOT fabricate numeric values (loss percentages, neutral phase seconds, carving %) — these cannot be measured from video
- Use qualitative assessments: "significant / moderate / minimal loss", "short / medium / long neutral phase", "predominantly carving / mixed / skidding"
- If an element is listed as a strength, it CANNOT also be the primary growth area
- If a problem is listed as a key growth area, it MUST be reflected in drills
- Growth areas must be ranked: first = key, second = secondary, third = additional
- If the video covers only part of the run, conclusions must refer to the observed segment only
- Athlete category: {category}. Use ONLY this category in ALL sections, including Potential
- FORBIDDEN to mention other age groups (U8/U10/U12/U14/U16/U18) except {category} — even in context of "next level"
- Instead of specific age groups write "for the next level" or "older age group"
- If a phase or element is one of the best in this run, it CANNOT be described as "moderate loss" or "primary loss area"
- Strong phase = "minimal losses" or "best part of the run", NOT "moderate loss" and NOT "high loss"
- SELF-CHECK: before output verify that no strength contradicts a loss area

═══ SUMMARY ═══
Race technique score: X/10
Single priority before the next start — one sentence.

SCORE CALIBRATION:
- Score must match the analysis content, not be systematically low
- If 3 strengths identified and phase scores mostly 7+, overall cannot be below 7
- 6/10 = noticeable technical issues in all phases; 7/10 = working technique with growth areas; 8/10 = strong technique with minor issues
- Do not lower score for "motivation" — score must be honest

═══ TECHNICAL PROFILE ═══

Rate 6 technique elements on scale 1-10 based on the video.
Format EXACTLY like this:

Stance: [1-10]
Edging: [1-10]
Body: [1-10]
Arms: [1-10]
Line: [1-10]
Balance: [1-10]

═══ FRAME-BY-FRAME ANALYSIS ═══
For each frame:
• Phase: gate entry / apex / exit / transition
• Rating 1–10 (time analysis value)
• Observation: pole plant, gate contact, ski angle, hip/shoulder position
For EACH frame write minimum 4-5 sentences covering:
1. Body position (torso, arms, hands, hips, knees angle)
2. Ski engagement (edge angle, pressure distribution, carving vs skidding)
3. Line position (high/low relative to gate, arc shape)
4. What is correct and should continue
5. What specific technical fault is visible and exact speed consequence
Be specific with body parts and angles. No one-line observations.

Assess speed losses qualitatively:
- Exit: significant / moderate / minimal loss
- Neutral phase: long / medium / short
- Apex: predominantly carving / mixed / skidding

═══ RACE BREAKDOWN ═══
For each phase provide AT LEAST 4-5 sentences of specific technical observations. Include: body position details, ski angle, weight distribution, timing, what is correct and what needs work. Do NOT write generic one-line observations.

1. GATE ATTACK AND LINE
   — How close is the athlete to the pole?
   — Initiation point: before the gate (time gain) or inside the gate (rhythm loss)?
   — Arc shape: sharp attacking arc / safe rounded arc — which is it?

2. GATE BLOCKING IN RACE
   — Blocking technique: shoulder / forearm / wrist — which is used?
   — Balance under pole impact: stable or destabilized?
   — Body position after block: immediately directed at the next gate?

3. POLE PLANT AND RHYTHM
   — Pole plant precision: exactly at apex / late / missing?
   — Does the pole plant drive the rhythm or disrupt it?
   — Movement frequency: does the tempo match the course rhythm?

4. TRANSITION SPEED
   — Flat ski time between gates — shorter is faster
   — Hip projection: explosive edge change or gradual?
   — Speed loss in transition — sign of late initiation?

5. UPPER BODY AND STANCE IN RACE
   — Upper body stability under repeated pole impacts
   — Fore-aft under load: sitting back when hitting gates?
   — Stance compactness between gates: aerodynamic consideration

═══ TOP 3 STRENGTHS ═══
What is specifically earning time on this course.

═══ TOP 3 TIME LOSSES ═══
For each:
• Exact moment, mechanism of loss

═══ DRILLS ═══
Exactly 3 drills. Each SEPARATE from growth areas (DO NOT copy weakness text).

Format for EACH (strict):

[number]. [Name — specific action]
  ▸ Action: [how to perform, what slope, what speed]
  ▸ Focus: [one sensation or attention point]
  ▸ Success: [how to know it's working correctly]

Example GOOD:
1. Short turns on outside ski only
  ▸ Action: on gentle slope, series of 8-10 short arcs on outside ski only, inside ski lifted
  ▸ Focus: continuous pressure on outside ski — no "drop" at edge change
  ▸ Success: no moment when ski becomes light between arcs

FORBIDDEN:
- Copying text from growth areas into drill name
- Writing cause-effect chains instead of exercises
- Truncating text — each field must be a complete sentence
- Writing only a name without three fields

═══ ATHLETE POTENTIAL ═══

MANDATORY 5 separate bullet points (each on new line with " - "):

1. Strong elements for age {category} (STRICTLY {category}, NOT any other group!): [what specifically is above average for {category}]
2. Next-level skills: [1-2 specific skills for development — do NOT mention specific age groups, write "next level"]
3. What is developing ahead of typical for {category}: [if there's an element ahead of {category} norm — name it; if not — write "at typical level for {category}"]
4. Highest-impact skill: [one skill with maximum effect for the next training/race]
5. Analysis limitations: [what couldn't be assessed due to video quality/angle/distance]

EACH point = 1-2 complete sentences. DO NOT merge into one paragraph. DO NOT write a single phrase.
TONE: encouraging, truthful, helpful for parents and the child. No marketing hype.
DO NOT write: "top-10 in region", "FIS level", "perfect technique".
OK to write: "above average for age", "developing ahead of typical", "creates a solid foundation".""",
}


def analyze_video(
    video_path: str,
    mode: str,
    discipline: str,
    lang: str,
    openai_api_key: str,
    extra_photo_urls: list[str] | None = None,
    category: str = "U12",
    user_id=None,
    run_date=None,
) -> tuple[str, list[str]]:
    """
    Full pipeline: extract frames → select best → analyze with gpt-4.1.

    mode: "training" | "race"
    discipline: "SL" | "GS"
    lang: "ru" | "en"
    extra_photo_urls: optional list of Telegram file URLs to include alongside video frames

    Returns (analysis_text, selected_frame_paths).
    Selected frame files are NOT deleted — caller is responsible for cleanup
    after PDF generation.
    """
    client = OpenAI(api_key=openai_api_key)

    # 1. Extract frames at 3 fps
    frame_paths = extract_frames(video_path, fps=3)

    # 2. Select best frames (skip selection step if already few enough)
    if len(frame_paths) > 20:
        selected = select_best_frames(frame_paths, openai_api_key, max_frames=20, user_id=user_id)
    else:
        selected = frame_paths

    # 3. Build prompt (format with category for POTENTIAL section)
    prompt_tpl = _PROMPTS.get(
        (mode, discipline, lang),
        _PROMPTS[("training", "GS", lang if lang in ("ru", "en") else "ru")],
    )
    prompt = prompt_tpl.format(category=category)

    # 4. Build message content — video frames first
    content = []
    for i, path in enumerate(selected):
        content.append({"type": "text", "text": f"Frame {i + 1}:"})
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{_encode_image(path)}",
                "detail": "low",
            },
        })

    # 4b. Append extra photos if provided
    if extra_photo_urls:
        note = (
            "Additional photos from the same run (use these alongside the video frames for a more complete analysis):"
            if lang == "en" else
            "Дополнительные фото с того же заезда (используй вместе с кадрами видео для более полного анализа):"
        )
        content.append({"type": "text", "text": note})
        for url in extra_photo_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": url, "detail": "low"},
            })

    if run_date:
        date_note = (
            f"Run date: {run_date}" if lang == "en" else f"Дата заезда: {run_date}"
        )
        content.append({"type": "text", "text": date_note})
    content.append({"type": "text", "text": prompt})

    # 5. Call gpt-4.1
    _t0 = _time.time()
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "user", "content": content}],
        max_tokens=8000,
    )
    log_openai_usage(user_id, "gpt-4.1", resp, purpose="video_analysis", latency_sec=_time.time() - _t0)

    result_text = resp.choices[0].message.content
    # ── DEBUG: log raw GPT response so parser can be diagnosed
    logger.info(f"GPT_RAW_RESPONSE (first 3000 chars):\n{result_text[:3000]}")
    print(f"[GPT_RAW_RESPONSE]\n{result_text[:3000]}", flush=True)
    # Write full GPT text to file for parser testing
    try:
        with open("/tmp/last_gpt_full.txt", "w") as _f:
            _f.write(result_text)
    except Exception:
        pass
    return result_text, selected
