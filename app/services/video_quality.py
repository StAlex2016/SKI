"""
Fast video quality pre-check using OpenCV.
Samples frames to detect blur, low motion, darkness before GPT analysis.
"""

import cv2


def analyze_video_quality(video_path: str) -> dict:
    """
    Sample every 10th frame and compute motion / sharpness / brightness metrics.

    Returns:
        score       float 1-10
        status      "GOOD" | "OK" | "BAD"
        useful_seconds  int
        issues      list[str]  (English)
        message_ru  str
        message_en  str
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return _error_result("Cannot open video file")

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration     = total_frames / fps

    # ── Sample every 10th frame ──────────────────────────────────────────────
    STEP = 10
    motion_scores: list[float] = []
    blur_scores:   list[float] = []
    brightnesses:  list[float] = []

    prev_gray    = None
    frame_idx    = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % STEP == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Blur score: Laplacian variance (higher = sharper)
            blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            blur_scores.append(blur)

            # Brightness: mean pixel value
            brightness = float(gray.mean())
            brightnesses.append(brightness)

            # Motion: pixel-level diff from previous sampled frame
            if prev_gray is not None:
                diff   = cv2.absdiff(gray, prev_gray)
                motion = float(diff.mean()) / 255.0
            else:
                motion = 0.0
            motion_scores.append(motion)

            prev_gray = gray

        frame_idx += 1

    cap.release()

    n = len(blur_scores)
    if n < 3:
        return _error_result("Video too short or unreadable")

    # ── Aggregate metrics ────────────────────────────────────────────────────
    avg_motion       = sum(motion_scores) / n
    avg_blur         = sum(blur_scores)   / n
    avg_bright       = sum(brightnesses)  / n

    low_motion_ratio = sum(1 for m in motion_scores if m < 0.01) / n
    dark_ratio       = sum(1 for b in brightnesses  if b < 40)   / n

    # Useful frame: passes motion + blur + brightness thresholds
    useful_ratio = sum(
        1 for m, bl, br in zip(motion_scores, blur_scores, brightnesses)
        if m >= 0.01 and bl >= 80 and br >= 40
    ) / n

    # ── Score formula ────────────────────────────────────────────────────────
    s_motion  = min(avg_motion * 20, 10.0)   # 0.5 avg → 10
    s_blur    = min(avg_blur / 50,   10.0)   # 500 laplacian → 10
    s_useful  = useful_ratio * 10.0

    raw_score = 0.4 * s_motion + 0.3 * s_blur + 0.3 * s_useful
    final_score = round(max(1.0, min(10.0, raw_score)), 1)

    # ── Status ───────────────────────────────────────────────────────────────
    if final_score >= 7.0 and useful_ratio > 0.6:
        status = "GOOD"
    elif final_score < 4.0 or useful_ratio < 0.3:
        status = "BAD"
    else:
        status = "OK"

    # ── Issues ───────────────────────────────────────────────────────────────
    issues_en: list[str] = []
    issues_ru: list[str] = []

    if low_motion_ratio > 0.4:
        issues_en.append("Too many static frames")
        issues_ru.append("Слишком много статичных кадров")
    if dark_ratio > 0.3:
        issues_en.append("Video too dark")
        issues_ru.append("Видео слишком тёмное")
    if avg_blur < 80:
        issues_en.append("Motion blur detected")
        issues_ru.append("Обнаружена смазанность кадров")
    if avg_motion < 0.01:
        issues_en.append("Athlete not visible enough")
        issues_ru.append("Спортсмен недостаточно виден")

    useful_seconds = int(useful_ratio * duration)

    # ── Messages ─────────────────────────────────────────────────────────────
    if status == "GOOD":
        msg_ru = "Отличное видео для анализа"
        msg_en = "Great video quality for analysis"
    elif status == "OK":
        msg_ru = "Видео подходит, точность может быть немного снижена"
        msg_en = "Video is suitable, accuracy may be slightly reduced"
    else:
        iss_ru = ". ".join(issues_ru) if issues_ru else "Низкое качество"
        iss_en = ". ".join(issues_en) if issues_en else "Low quality"
        msg_ru = f"Видео не подходит для анализа. {iss_ru}"
        msg_en = f"Video not suitable for analysis. {iss_en}"

    return {
        "score":          final_score,
        "status":         status,
        "useful_seconds": useful_seconds,
        "issues":         issues_en,
        "message_ru":     msg_ru,
        "message_en":     msg_en,
    }


# ── Internal helper ─────────────────────────────────────────────────────────

def _error_result(reason: str) -> dict:
    return {
        "score":          1.0,
        "status":         "BAD",
        "useful_seconds": 0,
        "issues":         [reason],
        "message_ru":     f"Видео не удалось обработать: {reason}",
        "message_en":     f"Could not process video: {reason}",
    }
