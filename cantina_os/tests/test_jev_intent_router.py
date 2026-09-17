"""
Unit tests for the Jev fast intent router's decision logic.

These tests never touch the network. They replay `fixtures/jev_recorded_responses.json` —
30 real Jev responses recorded against `jev-1.13.0` — through the pure decision functions, so
threshold, tier, veto and dedup behaviour is pinned without spending money or time.

The live-API test lives in `test_jev_integration_live.py`; the event-bus test in
`test_jev_e2e_bus.py`.
"""

import json
import os
from typing import Any, Dict, List

import pytest

from cantina_os.core.fast_router_gate import GATE, ActionTaken, FastRouterGate
from cantina_os.llm import jev_intents
from cantina_os.llm.jev_client import JevAnswer, JevResult

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "jev_recorded_responses.json")


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which errors on import.

    `tests/conftest.py` patches `deepgram.Deepgram` — a symbol that no longer exists in
    deepgram-sdk 5.x — so its autouse fixture raises AttributeError at setup for every test in
    the suite. These tests make no external calls at all (they replay recorded JSON), so
    shadowing it with a no-op is correct rather than merely convenient. The underlying
    conftest bug is pre-existing and left alone deliberately; it is reported separately.
    """
    yield {}


def _load() -> List[Dict[str, Any]]:
    with open(FIXTURE) as handle:
        return json.load(handle)


def _to_result(record: Dict[str, Any]) -> JevResult:
    """Rebuild a JevResult from a recorded response."""
    return JevResult(
        model="jev-1.13.0",
        answers={
            key: JevAnswer(
                type=value.get("type", ""),
                noul=value.get("noul"),
                choice=value.get("choice"),
                confidence=value.get("confidence"),
                probabilities=value.get("probabilities") or {},
            )
            for key, value in record["answers"].items()
        },
        input_tokens=record["usage"]["input_tokens"],
    )


RECORDS = _load()
BY_UTTERANCE = {record["utterance"]: record for record in RECORDS}


def decide_for(utterance: str, **kwargs: Any) -> jev_intents.RouterDecision:
    record = BY_UTTERANCE[utterance]
    return jev_intents.decide(_to_result(record), utterance, **kwargs)


# ------------------------------------------------------------------------------------------
# The property that matters most: conversation never fires a tool
# ------------------------------------------------------------------------------------------

CHAT_UTTERANCES = [record["utterance"] for record in RECORDS if record["expected"] is None]


def test_fixture_covers_both_classes():
    """Guard the fixture itself: a truncated recording would make these tests vacuous."""
    assert len(RECORDS) >= 25
    assert len(CHAT_UTTERANCES) >= 6
    assert any(record["expected"] for record in RECORDS)


@pytest.mark.parametrize("utterance", CHAT_UTTERANCES)
def test_conversation_never_dispatches(utterance):
    """No chat, question, fragment or past-tense observation may fire an action.

    This is the single most important behaviour in the router. A miss costs one slow turn; a
    false trigger blasts music into the room while someone was asking a question.
    """
    decision = decide_for(utterance)
    assert not decision.should_execute, (
        f"{utterance!r} wrongly dispatched {decision.intent!r} ({decision.reason})"
    )
    assert decision.intent is None


def test_past_tense_question_is_vetoed_not_just_low_scoring():
    """'did you turn the music down' must be caught, and we care *which* gate caught it.

    This is the utterance the benchmark identified as the reason the per-tool noul exists: on a
    choice-only design it scores high enough to execute.
    """
    decision = decide_for("did you turn the music down")
    assert not decision.should_execute
    # Either the choice landed on a no-tool label, or a gate vetoed it. Both are correct; what
    # would be wrong is dispatching.
    assert decision.choice in jev_intents.NO_TOOL_INTENTS or "vetoed" in decision.reason


# ------------------------------------------------------------------------------------------
# Correct dispatch
# ------------------------------------------------------------------------------------------

TOOL_CASES = [
    (record["utterance"], record["expected"]) for record in RECORDS if record["expected"]
]


@pytest.mark.parametrize("utterance,expected", TOOL_CASES)
def test_tool_utterances_never_fire_the_wrong_tool(utterance, expected):
    """A tool request may be declined (safe), but must never dispatch a *different* tool."""
    decision = decide_for(utterance)
    assert decision.intent in (None, expected), (
        f"{utterance!r} fired {decision.intent!r}, expected {expected!r} or a decline"
    )


def test_clear_commands_do_dispatch():
    """The router has to actually be useful, not just safe."""
    for utterance, expected in [
        ("hey Rex play some music", "play_music"),
        ("stop the music", "stop_music"),
        ("next track", "next_track"),
        ("start DJ mode", "dj_mode_on"),
        ("turn off dj mode", "dj_mode_off"),
        ("make your eyes blue", "set_eye_animation"),
    ]:
        decision = decide_for(utterance)
        assert decision.should_execute, f"{utterance!r} declined: {decision.reason}"
        assert decision.intent == expected


def test_noisy_stt_still_dispatches():
    """Deepgram mishears; the router must not be brittle about it."""
    for utterance in ["hey rex play sum music", "stop da music", "hey rex play some musci"]:
        decision = decide_for(utterance)
        assert decision.should_execute, f"{utterance!r} declined: {decision.reason}"


def test_decline_records_the_full_probability_map():
    """Every decline is an eval sample; the distribution has to survive into the decision."""
    decision = decide_for("quiet please")
    assert not decision.should_execute
    assert decision.probabilities, "probability map lost — the eval log would be useless"
    assert len(decision.top_two) == 2


# ------------------------------------------------------------------------------------------
# Thresholds and tiers
# ------------------------------------------------------------------------------------------

def test_threshold_is_configurable_and_actually_binds():
    """Raising the gate above the observed signal must stop a dispatch that otherwise happens."""
    utterance = "rex do your dj thing"  # recorded confidence ~0.85, the tightest true positive
    assert decide_for(utterance, threshold=0.5).should_execute
    assert not decide_for(utterance, threshold=0.999).should_execute


def test_impossible_threshold_declines_everything():
    """The kill switch: a gate above 1.0 for every tier must stop all dispatch.

    Note the free tier sits FREE_TIER_RELAXATION below `threshold`, so the impossible value
    has to clear that too — which is exactly the contract asserted below.
    """
    impossible = 1.01 + jev_intents.FREE_TIER_RELAXATION
    for record in RECORDS:
        decision = jev_intents.decide(
            _to_result(record), record["utterance"], threshold=impossible
        )
        assert not decision.should_execute, f"{record['utterance']!r} still fired"


def test_raising_the_threshold_tightens_the_free_tier_too():
    """One knob must move both tiers, or 'tighten the router' silently does half a job."""
    loose = jev_intents.tier_gate("next_track", threshold=0.85)
    tight = jev_intents.tier_gate("next_track", threshold=0.95)
    assert loose is not None and tight is not None
    assert tight["confidence"] > loose["confidence"]
    assert loose["confidence"] == pytest.approx(jev_intents.DEFAULT_FREE_THRESHOLD)


def test_zero_threshold_still_respects_the_command_veto():
    """Dropping the confidence gate must not open the door to conversation.

    The command gate is a separate axis on purpose — it is what makes the "cheap" tier safe.
    """
    decision = decide_for(
        "did you turn the music down", threshold=0.0, command_threshold=0.99
    )
    assert not decision.should_execute


def test_free_tier_gate_is_lower_than_cheap_tier():
    free = jev_intents.tier_gate("set_eye_animation")
    cheap = jev_intents.tier_gate("play_music")
    assert free is not None and cheap is not None
    assert free["confidence"] < cheap["confidence"]
    assert free["noul"] < cheap["noul"]


def test_committing_tier_can_never_fire():
    """The guard rail for any future write-to-shared-state tool."""
    assert jev_intents.tier_gate("play_music") is not None
    original = jev_intents.COMMITTING_TIER
    try:
        jev_intents.COMMITTING_TIER = frozenset({"play_music"})  # type: ignore[assignment]
        assert jev_intents.tier_gate("play_music") is None
        decision = decide_for("stop the music", threshold=0.0)
        assert decision.should_execute  # unaffected tool still works
        decision = decide_for("hey Rex play some music", threshold=0.0)
        assert not decision.should_execute
        assert decision.tier == "committing"
    finally:
        jev_intents.COMMITTING_TIER = original  # type: ignore[assignment]


def test_tiers_cover_every_tool_intent():
    """A new tool must be consciously tiered, not silently inherit a gate."""
    tiered = jev_intents.FREE_TIER | jev_intents.CHEAP_TIER | jev_intents.COMMITTING_TIER
    assert set(jev_intents.TOOL_INTENTS) == set(tiered)


# ------------------------------------------------------------------------------------------
# Unclear / malformed input
# ------------------------------------------------------------------------------------------

def test_none_result_declines():
    decision = jev_intents.decide(None, "play some music")
    assert not decision.should_execute
    assert "unavailable" in decision.reason


def test_empty_answers_decline():
    decision = jev_intents.decide(JevResult(model="x", answers={}), "play some music")
    assert not decision.should_execute


def test_unknown_choice_label_declines():
    """A model change that invents a new label must not crash or dispatch."""
    result = JevResult(
        model="x",
        answers={"intent": JevAnswer(type="choice", choice="launch_missiles", confidence=1.0)},
    )
    decision = jev_intents.decide(result, "do the thing")
    assert not decision.should_execute
    assert "unknown intent" in decision.reason


def test_missing_command_gate_does_not_veto_everything():
    """If the is_a_command question is absent it defaults open, not closed."""
    result = JevResult(
        model="x",
        answers={
            "intent": JevAnswer(type="choice", choice="stop_music", confidence=0.99),
            "stop_music": JevAnswer(type="noul", noul=0.99),
        },
    )
    assert jev_intents.decide(result, "stop the music").should_execute


def test_choice_without_agreeing_noul_declines():
    """The core design rule: high choice confidence alone is not enough."""
    result = JevResult(
        model="x",
        answers={
            "intent": JevAnswer(type="choice", choice="stop_music", confidence=0.99),
            "stop_music": JevAnswer(type="noul", noul=0.2),
            "is_a_command": JevAnswer(type="noul", noul=0.99),
        },
    )
    decision = jev_intents.decide(result, "stop the music")
    assert not decision.should_execute
    assert "noul=0.20" in decision.reason


# ------------------------------------------------------------------------------------------
# Parameter extraction
# ------------------------------------------------------------------------------------------

def test_eye_animation_requires_a_colour():
    """Without a colour the eye handler fails, so the router must decline instead."""
    assert jev_intents.extract_parameters("set_eye_animation", "change your eyes") is None
    params = jev_intents.extract_parameters("set_eye_animation", "make your eyes blue")
    assert params == {"color": "blue", "pattern": "solid"}


def test_eye_animation_picks_up_a_pattern_word():
    params = jev_intents.extract_parameters("set_eye_animation", "flash your eyes green")
    assert params == {"color": "green", "pattern": "flash"}


def test_colour_matching_is_whole_word_only():
    """'redo' must not read as 'red'."""
    assert jev_intents.extract_parameters("set_eye_animation", "redo that") is None


def test_play_music_passes_only_the_naming_words():
    """CHANGED 2026-09-17: this used to assert the whole utterance was passed as `track`.

    It was pinning the defect. Live, that produced
    `params={'track': 'Yeah. Go ahead and play some music for me.'}`, which the router turned
    into the non-existent `cantina_band`. See tests/test_jev_track_parameter.py.
    """
    params = jev_intents.extract_parameters("play_music", "  play the cantina band song ")
    assert params == {"track": "cantina band"}


def test_play_music_names_no_track_for_a_generic_request():
    assert jev_intents.extract_parameters("play_music", "play some music") == {"track": None}


def test_zero_parameter_intents():
    for intent in ("stop_music", "next_track", "dj_mode_on", "dj_mode_off"):
        assert jev_intents.extract_parameters(intent, "whatever") == {}


def test_eye_decline_surfaces_as_a_decision_not_an_exception():
    result = JevResult(
        model="x",
        answers={
            "intent": JevAnswer(type="choice", choice="set_eye_animation", confidence=0.99),
            "set_eye_animation": JevAnswer(type="noul", noul=0.99),
            "is_a_command": JevAnswer(type="noul", noul=0.99),
        },
    )
    decision = jev_intents.decide(result, "change your eyes somehow")
    assert not decision.should_execute
    assert "parameters are missing" in decision.reason


# ------------------------------------------------------------------------------------------
# State and question shape
# ------------------------------------------------------------------------------------------

def test_state_carries_identity_but_not_history():
    """Identity fixes Star Wars vocabulary; history made the router eager. Pin both."""
    state = jev_intents.build_state("play some music")
    assert set(state) == {"assistant", "utterance"}
    assert "DJ R3X" in state["assistant"]
    assert "recent_turns" not in state and "history" not in state


def test_questions_include_a_noul_per_tool_plus_the_gate():
    questions = jev_intents.build_questions()
    assert questions["intent"]["type"] == "choice"
    for intent in jev_intents.TOOL_INTENTS:
        assert questions[intent]["type"] == "noul"
    assert questions["is_a_command"]["type"] == "noul"


def test_choice_offers_both_no_tool_escapes():
    criteria = jev_intents.INTENT_CHOICE["criteria"]
    assert "general_chat" in criteria and "unclear" in criteria
    for intent in jev_intents.TOOL_INTENTS:
        assert intent in criteria


def test_no_volume_intents():
    """Volume was dropped deliberately: R3X has no volume tool and it caused the benchmark's
    worst confusions."""
    questions = jev_intents.build_questions()
    assert not any("volume" in key for key in questions)


# ------------------------------------------------------------------------------------------
# Dedup: the gate
# ------------------------------------------------------------------------------------------

@pytest.fixture
def gate():
    fresh = FastRouterGate()
    yield fresh
    fresh.reset()


def _action(transcript="play some music", intent="play_music"):
    import time as _t
    return ActionTaken(
        intent_name=intent, parameters={"track": transcript}, confidence=0.97,
        transcript=transcript, at=_t.time(),
    )


async def test_gate_is_inert_until_a_router_registers(gate):
    """With no router, Claude must not wait even a millisecond."""
    assert not gate.enabled
    assert await gate.wait_for_verdict("play some music", timeout_s=5.0) is None


async def test_gate_delivers_an_action_to_a_waiter(gate):
    import asyncio
    gate.register_router()

    async def resolve_soon():
        await asyncio.sleep(0.01)
        gate.resolve("play some music", _action())

    task = asyncio.create_task(resolve_soon())
    result = await gate.wait_for_verdict("play some music", timeout_s=1.0)
    await task
    assert result is not None
    assert result.intent_name == "play_music"


async def test_gate_handles_the_router_winning_the_race(gate):
    """The router usually resolves before Claude gets there; that must still be seen."""
    gate.register_router()
    gate.resolve("stop the music", _action("stop the music", "stop_music"))
    result = await gate.wait_for_verdict("stop the music", timeout_s=0.01)
    assert result is not None and result.intent_name == "stop_music"


async def test_gate_times_out_and_fails_open(gate):
    """A hung router must cost a bounded wait, not the turn."""
    import time as _t
    gate.register_router()
    started = _t.monotonic()
    result = await gate.wait_for_verdict("play some music", timeout_s=0.05)
    assert result is None
    assert _t.monotonic() - started < 0.5


async def test_gate_declined_verdict_returns_none(gate):
    gate.register_router()
    gate.resolve("tell me a joke", None)
    assert await gate.wait_for_verdict("tell me a joke", timeout_s=0.01) is None


def test_consume_is_single_shot(gate):
    """Dedup must not be re-usable: one action, one suppression."""
    gate.register_router()
    gate.resolve("play some music", _action())
    assert gate.consume("play some music") is not None
    assert gate.consume("play some music") is None


def test_records_expire(gate):
    """A stale record must never suppress a later turn's tool call."""
    import time as _t
    from cantina_os.core import fast_router_gate

    gate.register_router()
    stale = _action()
    stale.at = _t.time() - fast_router_gate.RECORD_TTL_S - 1
    gate._records["play some music"] = stale
    assert gate.peek("play some music") is None


def test_different_transcripts_do_not_collide(gate):
    gate.register_router()
    gate.resolve("play some music", _action())
    assert gate.peek("stop the music") is None
    assert gate.peek("play some music") is not None


async def test_unregister_releases_waiters(gate):
    """Router shutdown mid-turn must not leave Claude blocked for the full timeout."""
    import asyncio
    gate.register_router()

    async def shutdown_soon():
        await asyncio.sleep(0.01)
        gate.unregister_router()

    task = asyncio.create_task(shutdown_soon())
    assert await gate.wait_for_verdict("play some music", timeout_s=2.0) is None
    await task


def test_module_singleton_starts_disabled():
    """Import-time state must be inert so a test run or a router-less deploy is unaffected."""
    assert isinstance(GATE, FastRouterGate)
