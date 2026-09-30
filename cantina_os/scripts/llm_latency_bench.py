#!/usr/bin/env python
"""Latency bench: the same Sonnet 5.5 turn through OpenRouter, direct Anthropic, and a
Claude subscription (via the ``claude`` CLI).

What is measured, per turn
--------------------------
* **ttft** - request sent -> first streamed text token. This is what gates R3X starting
  to speak, so it is the number that matters for the voice loop.
* **total** - request sent -> stream finished.

Paths
-----
``openrouter``  anthropic SDK, ``base_url=https://openrouter.ai/api``, OPENROUTER_API_KEY.
``anthropic``   anthropic SDK against Anthropic directly, ANTHROPIC_API_KEY.
``sub-cold``    a fresh ``claude -p`` process per turn - what a naive subprocess call costs,
                CLI startup included.
``sub-warm``    one long-lived ``claude -p --input-format stream-json`` process fed a new
                user message each turn - CLI startup paid once, as a service would.
                (Its conversation grows by one short exchange per turn; negligible here.)

The subscription paths need the CLI signed in (``claude auth login``) or a
``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``. ``--bare`` is not used because it
refuses OAuth. A path with no credential is skipped, not failed.

Fairness: one discarded warm-up turn per path (the SDK clients are long-lived, like
ClaudeService's, so connections are reused afterwards), then rounds run round-robin so
network drift hits every path equally. Same system prompt, user line, effort and
max_tokens everywhere.

Usage
-----
    ../venv/bin/python scripts/llm_latency_bench.py                 # all available paths, 10 rounds
    ../venv/bin/python scripts/llm_latency_bench.py -n 20 --paths openrouter anthropic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(REPO), ".env"))

import anthropic  # noqa: E402

MODEL = "claude-sonnet-5-5"
OPENROUTER_MODEL = "anthropic/claude-sonnet-5.5"
OPENROUTER_BASE_URL = "https://openrouter.ai/api"  # the SDK appends /v1 itself
EFFORT = "low"
MAX_TOKENS = 200

SYSTEM = (
    "You are DJ R3X, the droid DJ at Oga's Cantina. Answer in one or two short, "
    "punchy spoken sentences. No markdown."
)
PROMPTS = [
    "Rex, what's your favourite planet?",
    "Give me a quick intro for the next track.",
    "How's the crowd tonight?",
    "Tell me a one-line droid joke.",
    "What are you playing next?",
]

Result = Tuple[float, float]  # (ttft_ms, total_ms)


# --------------------------------------------------------------------------- SDK paths


async def sdk_turn(client: anthropic.AsyncAnthropic, model: str, prompt: str) -> Result:
    t0 = time.perf_counter()
    ttft: Optional[float] = None
    async with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for event in stream:
            if (
                ttft is None
                and event.type == "content_block_delta"
                and event.delta.type == "text_delta"
            ):
                ttft = (time.perf_counter() - t0) * 1000
        await stream.get_final_message()
    total = (time.perf_counter() - t0) * 1000
    return (ttft if ttft is not None else total), total


# ---------------------------------------------------------------------- CLI (subscription)

CLI_ARGS = [
    "-p",
    "--model", MODEL,
    "--effort", EFFORT,
    "--system-prompt", SYSTEM,
    "--tools", "",
    "--setting-sources", "",
    "--no-session-persistence",
    "--output-format", "stream-json",
    "--include-partial-messages",
    "--verbose",
]


def _cli_env() -> Dict[str, str]:
    env = dict(os.environ)
    # An API key in the environment would make the CLI bill the API, not the subscription.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    return env


def _is_text_delta(msg: dict) -> bool:
    ev = msg.get("event") or {}
    return (
        msg.get("type") == "stream_event"
        and ev.get("type") == "content_block_delta"
        and (ev.get("delta") or {}).get("type") == "text_delta"
    )


async def _read_turn(proc: asyncio.subprocess.Process, t0: float) -> Result:
    ttft: Optional[float] = None
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            err = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(f"claude CLI exited early: {err.strip()[:300]}")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ttft is None and _is_text_delta(msg):
            ttft = (time.perf_counter() - t0) * 1000
        if msg.get("type") == "result":
            if msg.get("is_error"):
                raise RuntimeError(f"claude CLI error: {str(msg.get('result'))[:300]}")
            total = (time.perf_counter() - t0) * 1000
            return (ttft if ttft is not None else total), total


async def cli_cold_turn(prompt: str) -> Result:
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        "claude", *CLI_ARGS, prompt,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_cli_env(),
    )
    try:
        return await _read_turn(proc, t0)
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


class WarmCLI:
    """One long-lived ``claude -p`` process fed stream-json user messages."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            "claude", *CLI_ARGS, "--input-format", "stream-json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_cli_env(),
            limit=16 * 1024 * 1024,
        )

    async def turn(self, prompt: str) -> Result:
        assert self.proc and self.proc.stdin
        payload = {"type": "user", "message": {"role": "user", "content": prompt}}
        t0 = time.perf_counter()
        self.proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self.proc.stdin.drain()
        return await _read_turn(self.proc, t0)

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()


# --------------------------------------------------------------------------- harness


def _pct(xs: List[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--rounds", type=int, default=10)
    ap.add_argument(
        "--paths", nargs="+",
        default=["openrouter", "anthropic", "sub-warm", "sub-cold"],
        choices=["openrouter", "anthropic", "sub-warm", "sub-cold"],
    )
    ap.add_argument("--json", help="write raw samples to this file")
    args = ap.parse_args()

    runners = {}
    skipped: Dict[str, str] = {}
    warm: Optional[WarmCLI] = None

    for path in args.paths:
        if path == "openrouter":
            key = os.getenv("OPENROUTER_API_KEY")
            if not key:
                skipped[path] = "no OPENROUTER_API_KEY"
                continue
            c = anthropic.AsyncAnthropic(api_key=key, base_url=OPENROUTER_BASE_URL, max_retries=0)
            runners[path] = lambda p, c=c: sdk_turn(c, OPENROUTER_MODEL, p)
        elif path == "anthropic":
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                skipped[path] = "no ANTHROPIC_API_KEY"
                continue
            c = anthropic.AsyncAnthropic(api_key=key, max_retries=0)
            runners[path] = lambda p, c=c: sdk_turn(c, MODEL, p)
        elif shutil.which("claude") is None:
            skipped[path] = "claude CLI not on PATH"
        elif path == "sub-cold":
            runners[path] = cli_cold_turn
        elif path == "sub-warm":
            warm = WarmCLI()
            await warm.start()
            runners[path] = warm.turn

    # Warm-up: discarded, and doubles as the credential check.
    for path in list(runners):
        try:
            await runners[path]("Say hi.")
        except Exception as e:  # noqa: BLE001 - report and drop the path, keep the others
            skipped[path] = f"warm-up failed: {type(e).__name__}: {str(e)[:200]}"
            del runners[path]

    for path, why in skipped.items():
        print(f"  skip {path:<11} {why}")
    if not runners:
        print("Nothing to run.")
        return 1

    samples: Dict[str, List[Result]] = {p: [] for p in runners}
    errors: Dict[str, int] = {p: 0 for p in runners}
    print(f"\n{MODEL}, effort={EFFORT}, max_tokens={MAX_TOKENS}, {args.rounds} rounds, round-robin\n")
    for r in range(args.rounds):
        prompt = PROMPTS[r % len(PROMPTS)]
        line = []
        for path, run in runners.items():
            try:
                ttft, total = await run(prompt)
                samples[path].append((ttft, total))
                line.append(f"{path} {ttft:6.0f}/{total:6.0f}")
            except Exception as e:  # noqa: BLE001
                errors[path] += 1
                line.append(f"{path} ERR({type(e).__name__})")
        print(f"  round {r + 1:>2}: " + "   ".join(line))

    if warm:
        await warm.close()

    print(f"\n{'path':<11} {'n':>3} {'ttft p50':>9} {'p90':>7} {'min':>7} {'max':>7}   {'total p50':>9} {'p90':>7}  err")
    for path, xs in samples.items():
        if not xs:
            print(f"{path:<11}   0  (all turns failed)")
            continue
        t = [a for a, _ in xs]
        tot = [b for _, b in xs]
        print(
            f"{path:<11} {len(xs):>3} {statistics.median(t):>9.0f} {_pct(t, .9):>7.0f} "
            f"{min(t):>7.0f} {max(t):>7.0f}   {statistics.median(tot):>9.0f} {_pct(tot, .9):>7.0f}  {errors[path]}"
        )
    print("\n(ms; ttft = request sent -> first text token)")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"model": MODEL, "effort": EFFORT, "samples": samples, "skipped": skipped}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
