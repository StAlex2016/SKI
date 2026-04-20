"""OpenAI usage tracking: tokens + cost per call, stored as 'openai_call' events."""
from app.repositories import track


# ── PRICING (USD per 1M tokens, approximate public prices) ────────────────────
# Update when prices change or new models added.
_PRICING = {
    "gpt-4o-mini":    {"input": 0.15,  "output": 0.60},
    "gpt-4.1-mini":   {"input": 0.40,  "output": 1.60},
    "gpt-4.1":        {"input": 2.00,  "output": 8.00},
    "gpt-4o":         {"input": 2.50,  "output": 10.00},
}


def _cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD based on model and token counts. Returns 0.0 if model unknown."""
    p = _PRICING.get(model)
    if not p:
        return 0.0
    return round(
        (prompt_tokens / 1_000_000) * p["input"]
        + (completion_tokens / 1_000_000) * p["output"],
        6,
    )


def log_openai_usage(user_id, model: str, response, purpose: str, latency_sec: float = 0.0):
    """Extract token usage from OpenAI response and log as 'openai_call' event.

    Args:
        user_id: telegram user id (or None for system calls)
        model:   model name string (e.g. 'gpt-4.1')
        response: OpenAI ChatCompletion response with .usage
        purpose: semantic label e.g. 'photo_analysis' | 'video_analysis' | 'quality_check' | 'frame_selection'
        latency_sec: wall-clock latency of the call
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            pt = ct = tt = 0
        else:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            tt = getattr(usage, "total_tokens", 0) or (pt + ct)
        cost = _cost_usd(model, pt, ct)
        track(
            user_id,
            "openai_call",
            model=model,
            purpose=purpose,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cost_usd=cost,
            latency_sec=round(latency_sec, 2),
        )
    except Exception:
        # Never crash the bot because of tracking
        pass
