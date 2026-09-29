"""Benchmark ElevenLabs TTS models on R3X's real voice and request shape.

Measures, per model, over the same R3X-style lines:
  - TTFB: request sent -> first PCM chunk (what the listener waits for)
  - total: request sent -> last chunk
  - RTF: synthesis wall time / audio duration
  - intelligibility: WER of a Deepgram nova-3 transcript of the audio vs the source text

Requests mirror ElevenLabsService's streaming path (pcm_24000, same voice_settings). Model
order is shuffled per rep so drift in network conditions doesn't favour one model.

Usage:
    cd cantina_os && ../venv/bin/python scripts/bench_tts_models.py --reps 3 --out <dir>
"""

import argparse
import json
import os
import random
import re
import statistics
import time
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "P9l1opNa5pWou2X5MwfB")
SAMPLE_RATE = 24000

# Representative spoken replies: short confirmation, typical 2-3 sentence reply, a line full
# of proper nouns, numbers, and an audio-tag line (v4 claims tag support).
LINES = {
    "short": "Spinning it up now!",
    "typical": "Oh, you want the good stuff? Alright, here comes a classic from the Outer Rim. Try to keep up!",
    "names": "Welcome to Oga's Cantina on Batuu! I'm DJ R3X, formerly of Star Tours, now the best droid on the decks.",
    "numbers": "That track runs three minutes and forty-two seconds, and it's the 12th song tonight. Not bad for a Tuesday.",
    "question": "Wait, you're asking me what my favorite song is? Easy. Anything with a bass line that rattles my circuits.",
    "tags": "[laughing] Oh, that's a good one. [whispering] Don't tell the bartender, but I've been saving this track all night.",
}

# Mirrors ElevenLabsService voice_settings for the streaming path.
APP_SETTINGS = {"stability": 0.60, "similarity_boost": 0.85, "style": 0.25,
                "use_speaker_boost": True, "speed": 1.0}
# v4 docs: style and speed are unavailable.
V4_SETTINGS = {"stability": 0.60, "similarity_boost": 0.85}

MODELS = {
    "eleven_flash_v2_5": APP_SETTINGS,   # what CantinaOS runs today
    "eleven_turbo_v2_5": APP_SETTINGS,
    "eleven_v4_turbo": V4_SETTINGS,
    "eleven_v4": V4_SETTINGS,
}


def stream_once(client, model, text, settings):
    t0 = time.perf_counter()
    ttfb = None
    pcm = bytearray()
    for chunk in client.text_to_speech.stream(
        text=text, voice_id=VOICE_ID, model_id=model,
        voice_settings=settings, output_format="pcm_24000",
    ):
        if chunk:
            if ttfb is None:
                ttfb = time.perf_counter() - t0
            pcm.extend(chunk)
    total = time.perf_counter() - t0
    return ttfb, total, bytes(pcm)


def save_wav(path, pcm):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


def normalize(s):
    s = re.sub(r"\[[^\]]+\]", " ", s)          # audio tags are not spoken
    s = s.lower().replace("-", " ")
    return re.sub(r"[^a-z0-9' ]+", " ", s).split()


def wer(ref, hyp):
    r, h = normalize(ref), normalize(hyp)
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r)][len(h)] / max(len(r), 1)


def transcribe(path):
    resp = httpx.post(
        "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=false&numerals=false",
        headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}",
                 "Content-Type": "audio/wav"},
        content=Path(path).read_bytes(), timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=",".join(MODELS))
    args = ap.parse_args()
    out = Path(args.out)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    models = args.models.split(",")

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    # Compatibility probe: does each model accept the settings CantinaOS sends today?
    compat = {}
    for m in models:
        try:
            stream_once(client, m, "Test.", APP_SETTINGS)
            compat[m] = "accepted"
        except Exception as e:  # noqa: BLE001 - we want the API's message verbatim
            compat[m] = f"rejected: {str(e)[:300]}"
        print(f"[compat] {m}: {compat[m]}", flush=True)

    # Warm-up: one untimed request per model so TLS/connection setup isn't charged to rep 1.
    for m in models:
        stream_once(client, m, "Warming up.", MODELS[m])

    rows = []
    for rep in range(args.reps):
        for name, text in LINES.items():
            order = models[:]
            random.shuffle(order)
            for m in order:
                try:
                    ttfb, total, pcm = stream_once(client, m, text, MODELS[m])
                except Exception as e:  # noqa: BLE001
                    print(f"[err] {m} {name}: {e}", flush=True)
                    rows.append({"model": m, "line": name, "rep": rep, "error": str(e)[:300]})
                    continue
                dur = len(pcm) / 2 / SAMPLE_RATE
                row = {"model": m, "line": name, "rep": rep, "ttfb_ms": ttfb * 1000,
                       "total_ms": total * 1000, "audio_s": dur, "rtf": total / dur if dur else None}
                if rep == 0:
                    wav = out / "audio" / f"{m}__{name}.wav"
                    save_wav(wav, pcm)
                    row["wav"] = str(wav.relative_to(out))
                rows.append(row)
                print(f"[{rep}] {m:20s} {name:9s} ttfb={ttfb*1000:6.0f}ms total={total*1000:6.0f}ms audio={dur:4.1f}s", flush=True)

    # Intelligibility on the rep-0 audio.
    for row in rows:
        if row.get("wav"):
            try:
                hyp = transcribe(out / row["wav"])
                row["transcript"] = hyp
                row["wer"] = wer(LINES[row["line"]], hyp)
            except Exception as e:  # noqa: BLE001
                row["transcript_error"] = str(e)[:200]

    summary = {}
    for m in models:
        ok = [r for r in rows if r["model"] == m and "error" not in r]
        if not ok:
            summary[m] = {"n": 0}
            continue
        tt = sorted(r["ttfb_ms"] for r in ok)
        summary[m] = {
            "n": len(ok),
            "errors": sum(1 for r in rows if r["model"] == m and "error" in r),
            "ttfb_p50": statistics.median(tt),
            "ttfb_min": tt[0],
            "ttfb_max": tt[-1],
            "ttfb_p90": tt[min(len(tt) - 1, int(round(0.9 * (len(tt) - 1))))],
            "total_p50": statistics.median(r["total_ms"] for r in ok),
            "rtf_p50": statistics.median(r["rtf"] for r in ok if r["rtf"]),
            "mean_wer": statistics.mean(r["wer"] for r in ok if "wer" in r) if any("wer" in r for r in ok) else None,
        }

    result = {"voice_id": VOICE_ID, "reps": args.reps, "lines": LINES, "compat": compat,
              "summary": summary, "rows": rows}
    (out / "tts_results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"compat": compat, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
