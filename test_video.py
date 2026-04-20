import base64
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

FRAMES_DIR = Path("/home/ubuntu/ski-mvp-bot/test_frames")
FRAME_FILES = [
    "frame_020.jpg", "frame_030.jpg", "frame_040.jpg", "frame_050.jpg",
    "frame_060.jpg", "frame_070.jpg", "frame_080.jpg",
]

MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"]

COSTS = {
    "gpt-4o":       {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini":  {"input": 0.00015, "output": 0.0006},
    "gpt-4.1":      {"input": 0.002,  "output": 0.008},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
}

PROMPT = (
    "You are a professional alpine skiing coach. These are 7 frames from a 45-second ski run "
    "(frames taken every ~3 seconds).\n"
    "Analyze:\n"
    "1. Which frames show the best technique moments (transition, edge change, apex, exit)?\n"
    "2. Rate each frame 1-10 for analysis value\n"
    "3. Overall technique assessment: stance, balance, line, speed efficiency\n"
    "4. Top 3 strengths and top 3 areas for improvement\n"
    "Be specific and professional."
)


def load_frames():
    frames = []
    for fname in FRAME_FILES:
        path = FRAMES_DIR / fname
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        frames.append((fname, data))
    return frames


def build_messages(frames):
    content = []
    for fname, b64 in frames:
        content.append({"type": "text", "text": f"Frame: {fname}"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })
    content.append({"type": "text", "text": PROMPT})
    return [{"role": "user", "content": content}]


def estimate_cost(model, input_tokens, output_tokens):
    c = COSTS[model]
    return (input_tokens / 1000 * c["input"]) + (output_tokens / 1000 * c["output"])


def run_model(model, messages):
    print(f"\n{'='*70}")
    print(f"MODEL: {model}")
    print("="*70)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1500,
        )
        text = resp.choices[0].message.content
        in_tok = resp.usage.prompt_tokens
        out_tok = resp.usage.completion_tokens
        cost = estimate_cost(model, in_tok, out_tok)

        print(f"\n--- Response ---\n{text}")
        print(f"\n--- Usage ---")
        print(f"  Input tokens:  {in_tok}")
        print(f"  Output tokens: {out_tok}")
        print(f"  Estimated cost: ${cost:.5f}")
        return {"model": model, "in_tok": in_tok, "out_tok": out_tok, "cost": cost, "ok": True}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"model": model, "in_tok": 0, "out_tok": 0, "cost": 0.0, "ok": False}


def print_table(results):
    print(f"\n{'='*70}")
    print("COMPARISON TABLE")
    print("="*70)
    header = f"{'Model':<16} {'Quality est.':<14} {'Total cost':>12} {'Input tok':>10} {'Output tok':>11}"
    print(header)
    print("-" * 70)
    for r in results:
        quality = "N/A" if not r["ok"] else (
            "High"   if r["model"] in ("gpt-4o", "gpt-4.1") else "Medium"
        )
        cost_str = f"${r['cost']:.5f}" if r["ok"] else "ERROR"
        print(f"{r['model']:<16} {quality:<14} {cost_str:>12} {r['in_tok']:>10} {r['out_tok']:>11}")
    print("="*70)
    if any(r["ok"] for r in results):
        total = sum(r["cost"] for r in results)
        print(f"  Total cost for all models: ${total:.5f}")


def main():
    print("Loading frames...")
    frames = load_frames()
    print(f"Loaded {len(frames)} frames from {FRAMES_DIR}")

    messages = build_messages(frames)
    results = []
    for model in MODELS:
        result = run_model(model, messages)
        results.append(result)

    print_table(results)


if __name__ == "__main__":
    main()
