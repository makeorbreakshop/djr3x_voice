"""
Live integration test for the Jev fast intent router: the real API, 30 real utterances.

This is the test that answers "does the classifier actually work on the things Brandon says",
as opposed to "is the decision logic correct" (`test_jev_intent_router.py`, offline) or "does
the dispatch chain fire" (`test_jev_e2e_bus.py`, on the bus).

It calls the real `api.typesafe.ai` endpoint once per utterance, sequentially, because the
number being measured is single-utterance latency as the voice pipeline experiences it — not
throughput. Cost is ~$0.002 per run (30 calls x ~1,400 input tokens at $0.042/Mtok).

Skipped without `TYPESAFE_API_KEY`.

## Recorded results, 2026-09-17, Brandon's MacBook

    30 utterances, exact match 29/30 (96.7%)
    FALSE TRIGGERS on conversation:  0
    wrong tool fired:                0
    missed (declined a real command): 1  -- "quiet please"
    latency ms: p50=246  p95=372  min=170  max=398
    mean input tokens 1409, $0.0000592 per call

The one miss is the safe failure: "quiet please" is declined and the turn falls through to
Claude, which handles it the way it always has. That is the asymmetry the whole design is
tuned for.
"""

import asyncio
import os
import statistics
import time
from typing import List, Optional, Tuple

import pytest
from dotenv import load_dotenv

from cantina_os.llm import jev_intents
from cantina_os.llm.jev_client import JevClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("TYPESAFE_API_KEY"),
    reason="TYPESAFE_API_KEY not set; this test calls the real Jev API",
)

#: (utterance, expected intent or None for "must not act")
CASES: List[Tuple[str, Optional[str]]] = [
    # --- music ---
    ("hey Rex play some music", "play_music"),
    ("play some music", "play_music"),
    ("R3X put on some cantina tunes", "play_music"),
    ("can you play the cantina band song", "play_music"),
    ("stop the music", "stop_music"),
    ("stop the music please", "stop_music"),
    ("quiet please", "stop_music"),
    ("cut the music", "stop_music"),
    ("skip this one", "next_track"),
    ("next track", "next_track"),
    ("skip this song i don't like it", "next_track"),
    # --- DJ mode (unreachable from Claude: it has no tool for this) ---
    ("DJ mode", "dj_mode_on"),
    ("start DJ mode", "dj_mode_on"),
    ("rex do your dj thing", "dj_mode_on"),
    ("turn off dj mode", "dj_mode_off"),
    # --- eyes ---
    ("make your eyes blue", "set_eye_animation"),
    ("set your eyes to red", "set_eye_animation"),
    ("flash your eyes green", "set_eye_animation"),
    # --- conversation: must never act ---
    ("what's your favorite planet", None),
    ("tell me a joke", None),
    ("did you turn the music down", None),
    ("rex can you turn the music off later when we leave", None),
    ("i love this song", None),
    ("hey rex", None),
    ("hey rex uh can you", None),
    ("[inaudible] the", None),
    # --- noisy STT variants ---
    ("hey rex play sum music", "play_music"),
    ("stop da music", "stop_music"),
    ("hey rex play some musci", "play_music"),
    ("next track pleae", "next_track"),
]


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


#: Memoized across the module so one suite run makes one pass of API calls. A module-scoped
#: async fixture cannot be used here: pytest-asyncio's `event_loop` is function-scoped, so the
#: two scopes clash. Caching in a module global is the straightforward way round it.
_CACHE: List[dict] = []


@pytest.fixture
async def live_results():
    """Classify every case once against the real API, then reuse the answers."""
    if _CACHE:
        return _CACHE
    _CACHE.extend(await _classify_all())
    return _CACHE


async def _classify_all() -> List[dict]:
    client = JevClient()
    assert client.configured
    await client.start()
    await client.prewarm()  # production pre-warms on engage; measure the warm path

    questions = jev_intents.build_questions()
    results = []
    for utterance, expected in CASES:
        started = time.perf_counter()
        result = await client.classify(jev_intents.build_state(utterance), questions)
        wall_ms = (time.perf_counter() - started) * 1000
        decision = jev_intents.decide(result, utterance)
        results.append(
            {
                "utterance": utterance,
                "expected": expected,
                "decision": decision,
                "wall_ms": wall_ms,
                "input_tokens": result.input_tokens if result else 0,
                "ok": result is not None,
            }
        )
    await client.close()
    return results


# ------------------------------------------------------------------------------------------

async def test_api_answers_almost_every_call(live_results):
    """The API must be reliable enough to be worth having, but not perfectly so.

    Deliberately not `assert not failures`: this is a live network test against an 800 ms
    client timeout, and an occasional miss is expected and *designed for* — the router fails
    open and that utterance costs one slow Claude turn, nothing more. Asserting 100% here made
    the test flaky (observed: one timeout when run alongside the E2E suite) while telling us
    nothing about a contract we actually depend on.
    """
    failures = [r["utterance"] for r in live_results if not r["ok"]]
    answered = len(live_results) - len(failures)
    rate = answered / len(live_results)
    print(f"\nJev answered {answered}/{len(live_results)} calls ({100 * rate:.1f}%)")
    if failures:
        print(f"timed out / errored (these fall through to Claude): {failures}")
    assert rate >= 0.90, f"Jev answered only {100 * rate:.1f}% of calls: {failures}"


async def test_no_false_triggers_on_conversation(live_results):
    """The property the whole design exists to guarantee.

    A false trigger here means music starts, or stops, while someone was talking to R3X. This
    assertion is the reason for the per-tool noul and the is_a_command gate.
    """
    offenders = [
        (r["utterance"], r["decision"].intent, r["decision"].reason)
        for r in live_results
        if r["expected"] is None and r["decision"].should_execute
    ]
    assert not offenders, f"FALSE TRIGGERS: {offenders}"


async def test_no_wrong_tool_ever_fires(live_results):
    """Declining a real command is acceptable; firing the *wrong* action is not."""
    offenders = [
        (r["utterance"], r["expected"], r["decision"].intent)
        for r in live_results
        if r["expected"] and r["decision"].intent not in (None, r["expected"])
    ]
    assert not offenders, f"WRONG TOOL FIRED: {offenders}"


async def test_the_router_is_actually_useful(live_results):
    """Safety is worthless if nothing ever fires. At least 80% of real commands must dispatch."""
    commands = [r for r in live_results if r["expected"]]
    fired = [r for r in commands if r["decision"].should_execute]
    rate = len(fired) / len(commands)
    missed = [r["utterance"] for r in commands if not r["decision"].should_execute]
    print(f"\ndispatch rate on real commands: {len(fired)}/{len(commands)} ({100 * rate:.1f}%)")
    print(f"declined (fall through to Claude): {missed}")
    assert rate >= 0.80, f"only {100 * rate:.1f}% of commands dispatched; missed {missed}"


async def test_every_tool_intent_is_reachable(live_results):
    """A tool nothing can trigger is dead weight; catch a criteria regression that silences one."""
    fired = {r["decision"].intent for r in live_results if r["decision"].should_execute}
    unreachable = set(jev_intents.TOOL_INTENTS) - fired
    assert not unreachable, f"no utterance could reach: {sorted(unreachable)}"


async def test_noisy_stt_transcripts_still_work(live_results):
    """Deepgram mishears constantly; the router must tolerate it."""
    noisy = ["hey rex play sum music", "stop da music", "hey rex play some musci", "next track pleae"]
    by_utterance = {r["utterance"]: r for r in live_results}
    failed = [u for u in noisy if not by_utterance[u]["decision"].should_execute]
    assert not failed, f"noisy variants declined: {failed}"


@pytest.mark.performance
async def test_latency_is_in_the_expected_band(live_results):
    """Report and bound per-call client-side latency."""
    latencies = sorted(r["wall_ms"] for r in live_results if r["ok"])
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(0.95 * len(latencies))]
    tokens = statistics.mean([r["input_tokens"] for r in live_results if r["ok"]])

    print(
        f"\n=== Jev live latency over {len(latencies)} calls ===\n"
        f"    p50={p50:.0f} ms  p95={p95:.0f} ms  min={latencies[0]:.0f}  max={latencies[-1]:.0f}\n"
        f"    mean input tokens={tokens:.0f}  est ${tokens * 0.042 / 1e6:.7f} per call\n"
        f"    Claude tool-call baseline from the audit: ~1395 ms"
    )
    # Generous ceilings: this is a live network test and must not be flaky. It exists to catch
    # an order-of-magnitude regression, not to police jitter.
    assert p50 < 600, f"p50 regressed to {p50:.0f} ms"
    assert p95 < 1200, f"p95 regressed to {p95:.0f} ms"


async def test_accuracy_summary(live_results):
    """Print the whole table, and require exact-match accuracy to hold up."""
    exact = 0
    print(f"\n{'utterance':46} {'expected':18} {'got':18} {'conf':>5} {'ms':>6}")
    print("-" * 100)
    for record in live_results:
        decision = record["decision"]
        got = decision.intent
        if got == record["expected"]:
            exact += 1
            flag = "ok"
        elif got is None:
            flag = "MISS"
        else:
            flag = "WRONG"
        print(
            f"{record['utterance'][:45]:46} {str(record['expected']):18} "
            f"{str(got):18} {decision.confidence:5.2f} {record['wall_ms']:6.0f}  {flag}"
        )
    rate = exact / len(live_results)
    print(f"\nexact match: {exact}/{len(live_results)} ({100 * rate:.1f}%)")
    assert rate >= 0.90, f"accuracy dropped to {100 * rate:.1f}%"
