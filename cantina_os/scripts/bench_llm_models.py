"""Benchmark Claude models on R3X's real spoken-reply request.

Builds the request exactly as ClaudeService._stream_claude_response does - the live persona
system prompt and registered tool schemas (both with cache_control), SPOKEN_REPLY_MAX_TOKENS,
TEMPERATURE - through the same provider resolution (llm/anthropic_provider.py), then varies
only the model and thinking/effort settings per arm.

Per call it records:
  - ttft_ms:  request -> first text delta (when TTS could start speaking)
  - tool_ms:  request -> first tool_use block start (when an action could fire)
  - total_ms, output tokens, stop_reason, tools called, spoken text

Quality: tool-choice accuracy against an expected tool per utterance, truncation
(stop_reason == max_tokens), empty spoken replies, and a blind LLM judge on rep-0 replies.

Usage:
    cd cantina_os && env -u ANTHROPIC_BASE_URL ../venv/bin/python scripts/bench_llm_models.py --out <dir>

(ANTHROPIC_BASE_URL is unset because Claude Code sessions export it, which would override
the provider's host. The app itself doesn't run with it set.)
"""

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

import anthropic
from anthropic import Anthropic
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "cantina_os"))

from cantina_os.llm.anthropic_provider import client_kwargs, map_model, resolve_provider  # noqa: E402
from cantina_os.services.claude_service.claude_service import ClaudeService  # noqa: E402

ACTION_PLAY = (
    "<action_already_taken>\n"
    "  <tool>play_music</tool>\n"
    "  <parameters>track_query='Cantina Band'</parameters>\n"
    "  <status>Already executed. This is done and the user can already see or hear "
    "the result.</status>\n"
    "  <your_instructions>Do NOT call any tool for this request - it has already been "
    "carried out for you. Respond with one short spoken line, in character, reacting "
    "to what you just did. Speak about it in the past or present tense, never as "
    "something you are about to do.</your_instructions>\n"
    "</action_already_taken>\n\n"
    "<user_input>\nput on some cantina tunes\n</user_input>"
)

# (id, user message, allowed tool sets, suppress_tools)
# An empty frozenset means "should just talk".
CASES = [
    ("chat_night", "Hey R3X, how's your night going?", [frozenset()], False),
    ("chat_fav", "What's your favorite song?", [frozenset()], False),
    ("chat_joke", "Tell me a joke about Wookiees.", [frozenset()], False),
    ("ambig_down", "Did you turn the music down?", [frozenset()], False),
    ("play", "Play some cantina music.", [frozenset({"play_music"}), frozenset({"search_music"})], False),
    ("play_mood", "Put on something upbeat for a party.", [frozenset({"play_music"}), frozenset({"search_music"})], False),
    ("stop", "Stop the music.", [frozenset({"stop_music"})], False),
    ("eyes", "Turn your eyes red.", [frozenset({"set_eye_color"})], False),
    ("see", "What do you see right now?", [frozenset({"analyze_scene"})], False),
    ("action_taken", ACTION_PLAY, [frozenset()], True),
]

APP = "app"  # sentinel: send the app's own temperature and no thinking param, as today

ARMS = {
    # name: (model, thinking, effort, send_temperature)
    "sonnet5_prod":        ("claude-sonnet-5",   None,                       None,  True),
    "sonnet55_dropin":     ("claude-sonnet-5-5", None,                       None,  True),
    "sonnet55_bt_low":     ("claude-sonnet-5-5", {"type": "between_tools"},  "low", False),
    "sonnet55_bt_medium":  ("claude-sonnet-5-5", {"type": "between_tools"},  "medium", False),
    "sonnet5_off_low":     ("claude-sonnet-5",   {"type": "disabled"},       "low", False),
    "haiku45_prod":        ("claude-haiku-4-5",  None,                       None,  True),
}

# OpenRouter ids for models not yet in OPENROUTER_MODEL_MAP.
EXTRA_OR_IDS = {"claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
                "claude-opus-5-5": "anthropic/claude-opus-5.5"}


def resolve_model(model, provider):
    if provider.is_openrouter and model in EXTRA_OR_IDS:
        return EXTRA_OR_IDS[model]
    return map_model(model, provider.provider)


async def load_app_request():
    svc = ClaudeService(event_bus=AsyncIOEventEmitter(), config={})
    svc._register_command_functions()
    system = [{"type": "text", "text": svc._config["SYSTEM_PROMPT"],
               "cache_control": {"type": "ephemeral"}}]
    return system, svc._get_tool_schemas_with_cache(), svc._config


def run_call(client, model, arm, case, system, tools, cfg):
    _, thinking, effort, send_temp = arm
    cid, user, _, suppress = case
    kw = dict(model=model, max_tokens=cfg["SPOKEN_REPLY_MAX_TOKENS"], system=system,
              messages=[{"role": "user", "content": user}], tools=tools)
    if send_temp:
        kw["temperature"] = cfg["TEMPERATURE"]
    if thinking:
        kw["thinking"] = thinking
    if effort:
        kw["output_config"] = {"effort": effort}
    if suppress:
        kw["tool_choice"] = {"type": "none"}

    t0 = time.perf_counter()
    ttft = tool_t = None
    with client.messages.stream(**kw) as stream:
        for ev in stream:
            now = time.perf_counter() - t0
            if ev.type == "content_block_start" and ev.content_block.type == "tool_use" and tool_t is None:
                tool_t = now
            elif ev.type == "content_block_delta" and ev.delta.type == "text_delta" and ev.delta.text and ttft is None:
                ttft = now
        msg = stream.get_final_message()
    total = time.perf_counter() - t0
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    tools_called = [{"name": b.name, "input": b.input} for b in msg.content if b.type == "tool_use"]
    u = msg.usage
    return {
        "ttft_ms": ttft * 1000 if ttft is not None else None,
        "tool_ms": tool_t * 1000 if tool_t is not None else None,
        "total_ms": total * 1000,
        "stop_reason": msg.stop_reason,
        "text": text,
        "tools": tools_called,
        "block_types": [b.type for b in msg.content],
        "output_tokens": u.output_tokens,
        "input_tokens": u.input_tokens,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def first_response_ms(r):
    """When the user first gets *something*: the action firing or R3X starting to speak."""
    xs = [x for x in (r.get("ttft_ms"), r.get("tool_ms")) if x is not None]
    return min(xs) if xs else None


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None


JUDGE_PROMPT = """You are grading spoken replies from DJ R3X, the droid DJ at Oga's Cantina (Star Wars: Galaxy's Edge). Replies are converted straight to speech, so they must be short (1-3 sentences), fun, in character, and contain no markdown, emoji, stage directions or lists.

The user said: {user}
{context}
Below are anonymous replies from different systems. Score each 1-5 on:
- character: sounds like a witty, energetic droid DJ
- speakable: short and natural to hear aloud (penalise length, markdown, stage directions, emoji)
- correct: responds sensibly to what was asked (and, if an action was already done, talks about it as done)

Return ONLY JSON: {{"scores": {{"<id>": {{"character": n, "speakable": n, "correct": n}}, ...}}}}

Replies:
{replies}"""


def judge(client, judge_model, rows_by_case):
    out = {}
    for cid, items in rows_by_case.items():
        case = next(c for c in CASES if c[0] == cid)
        user = re.sub(r"<[^>]+>", "", case[1]).strip().splitlines()[-1] if case[3] else case[1]
        context = ("(The play_music action - 'Cantina Band' - had already been executed before R3X replied.)\n"
                   if case[3] else "")
        items = [i for i in items if i["reply"]]
        random.shuffle(items)
        labels = {f"r{n}": i["arm"] for n, i in enumerate(items)}
        replies = "\n".join(f'[{k}] "{i["reply"]}"' for k, i in zip(labels, items))
        resp = client.messages.create(
            model=judge_model, max_tokens=4000,
            messages=[{"role": "user", "content": JUDGE_PROMPT.format(user=user, context=context, replies=replies)}],
        )
        raw = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", raw, re.S)
        scores = json.loads(m.group(0))["scores"] if m else {}
        for k, arm in labels.items():
            if k in scores:
                out.setdefault(arm, []).append(scores[k])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--judge-model", default="claude-opus-5-5")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    arms = args.arms.split(",")

    provider = resolve_provider({})
    assert provider, "no Anthropic-compatible credential"
    client = Anthropic(**client_kwargs(provider), max_retries=1)
    system, tools, cfg = asyncio.run(load_app_request())
    print(f"provider={provider.provider} persona_chars={len(system[0]['text'])} tools={[t['name'] for t in tools]}", flush=True)

    # Warm each model's prompt cache so rep 0 isn't charged a cache write the app wouldn't pay.
    for a in arms:
        try:
            run_call(client, resolve_model(ARMS[a][0], provider), ARMS[a], CASES[0], system, tools, cfg)
        except anthropic.APIStatusError as e:
            print(f"[warm] {a}: {e.status_code} {e.message[:200]}", flush=True)

    rows = []
    for rep in range(args.reps):
        for case in CASES:
            order = arms[:]
            random.shuffle(order)
            for a in order:
                model = resolve_model(ARMS[a][0], provider)
                row = {"arm": a, "model": model, "case": case[0], "rep": rep}
                try:
                    row.update(run_call(client, model, ARMS[a], case, system, tools, cfg))
                except anthropic.APIStatusError as e:
                    row["error"] = f"{e.status_code} {e.message[:300]}"
                except Exception as e:  # noqa: BLE001
                    row["error"] = repr(e)[:300]
                if "error" not in row:
                    called = frozenset(t["name"] for t in row["tools"])
                    row["tool_ok"] = called in case[2]
                    row["first_ms"] = first_response_ms(row)
                rows.append(row)
                if "error" in row:
                    print(f"[{rep}] {a:19s} {case[0]:12s} ERROR {row['error'][:120]}", flush=True)
                else:
                    ttft = f"{row['ttft_ms']:.0f}" if row["ttft_ms"] else "-"
                    tl = f"{row['tool_ms']:.0f}" if row["tool_ms"] else "-"
                    print(f"[{rep}] {a:19s} {case[0]:12s} ttft={ttft:>5} tool={tl:>5} total={row['total_ms']:5.0f} "
                          f"out={row['output_tokens']:3d} stop={row['stop_reason']:10s} ok={row['tool_ok']} "
                          f"{[t['name'] for t in row['tools']]} {row['text'][:70]!r}", flush=True)

    # Blind judge on rep-0 replies.
    by_case = {}
    for r in rows:
        if r["rep"] == 0 and "error" not in r:
            by_case.setdefault(r["case"], []).append({"arm": r["arm"], "reply": r["text"]})
    judge_scores = {}
    try:
        judge_scores = judge(client, resolve_model(args.judge_model, provider), by_case)
    except Exception as e:  # noqa: BLE001
        print(f"[judge] failed: {e!r}"[:300], flush=True)

    summary = {}
    for a in arms:
        ok = [r for r in rows if r["arm"] == a and "error" not in r]
        errs = [r for r in rows if r["arm"] == a and "error" in r]
        if not ok:
            summary[a] = {"n": 0, "errors": len(errs), "first_error": errs[0]["error"] if errs else None}
            continue
        first = [r["first_ms"] for r in ok if r["first_ms"] is not None]
        ttft = [r["ttft_ms"] for r in ok if r["ttft_ms"] is not None]
        tool = [r["tool_ms"] for r in ok if r["tool_ms"] is not None]
        chat = [r for r in ok if not next(c for c in CASES if c[0] == r["case"])[2][0]]
        js = judge_scores.get(a, [])
        summary[a] = {
            "model": ok[0]["model"], "n": len(ok), "errors": len(errs),
            "first_p50": statistics.median(first) if first else None, "first_p90": pct(first, 0.9),
            "ttft_p50": statistics.median(ttft) if ttft else None, "ttft_p90": pct(ttft, 0.9),
            "tool_p50": statistics.median(tool) if tool else None,
            "total_p50": statistics.median(r["total_ms"] for r in ok),
            "tool_accuracy": sum(r["tool_ok"] for r in ok) / len(ok),
            "truncated": sum(r["stop_reason"] == "max_tokens" for r in ok),
            "empty_chat_replies": sum(1 for r in chat if not r["text"]),
            "chat_n": len(chat),
            "out_tokens_p50": statistics.median(r["output_tokens"] for r in ok),
            "reply_chars_p50": statistics.median(len(r["text"]) for r in ok if r["text"]) if any(r["text"] for r in ok) else 0,
            "cache_hit_rate": sum(1 for r in ok if r["cache_read"] > 0) / len(ok),
            "judge": {k: statistics.mean(s[k] for s in js) for k in ("character", "speakable", "correct")} if js else None,
        }

    (out / "llm_results.json").write_text(json.dumps(
        {"provider": provider.provider, "arms": {a: [str(x) for x in ARMS[a]] for a in arms},
         "summary": summary, "rows": rows}, indent=2, default=list))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
