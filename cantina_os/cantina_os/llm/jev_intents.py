"""
Jev question catalogue and decision logic for DJ R3X's fast intent router.

This module is deliberately pure — no event bus, no HTTP, no service. Everything here is a
constant or a function of its arguments, so the decision rules can be unit-tested against
recorded Jev responses without touching the network.

## Why these questions

The catalogue implements "design D" from the benchmark in
``~/machines-for-makers/outputs/voice-assistants/jev-router/`` (66 labelled utterances, two
passes, eight competing designs). Design D asks three overlapping things in a single round trip:

1. ``intent`` — a Choice over every tool plus ``general_chat`` / ``unclear``. Decides *which*.
2. one Noul per tool — "is the speaker asking for *this*, right now?". A second, independent
   read of the same utterance.
3. ``is_a_command`` — "is this an instruction at all, or conversation?". The gate that stops
   *"did you turn the music down"* from turning the music down.

A tool fires only where all three agree. In the benchmark that combination was the only arm with
**0 % wrongly-executed actions and 0 % false triggers on conversation**, at p50 193 ms — versus
4.1 % wrongly-executed for the single-Choice design. That asymmetry is the whole point: a missed
trigger costs the user a slow-but-correct Claude turn, whereas a false trigger blasts music
into the room while they were asking a question. We buy safety with misses.

The benchmark's ``volume_up`` / ``volume_down`` intents are deliberately **omitted** — R3X has no
volume tool, and they were the source of the benchmark's worst confusions (*"quiet please"* →
``volume_down``, *"did you turn the music down"* → ``volume_down``). Dropping them removes that
entire error class.

Wording follows the jev-1.13 jaggedness guidance: literal conditions in ``instructions``,
boundary cases pushed into ``criteria``, nothing requiring arithmetic or a second hop.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.track_request import naming_phrase

# --------------------------------------------------------------------------------------------
# Intents
# --------------------------------------------------------------------------------------------

#: Intents that dispatch a deterministic action with no LLM in the loop. Each one maps to a
#: handler in ``IntentRouterService``.
TOOL_INTENTS: List[str] = [
    "play_music",
    "stop_music",
    "next_track",
    "dj_mode_on",
    "dj_mode_off",
    "set_eye_animation",
]

#: The two outcomes that dispatch nothing and hand the whole turn to Claude.
NO_TOOL_INTENTS = frozenset({"general_chat", "unclear"})

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "play_music": "Start playing music, or play a particular song, artist, or playlist.",
    "stop_music": "Stop or pause the music that is currently playing.",
    "next_track": "Skip the current track and play the next one.",
    "dj_mode_on": "Turn on DJ mode, where the droid talks over the music like a club DJ.",
    "dj_mode_off": "Turn off DJ mode and go back to normal behaviour.",
    "set_eye_animation": "Change the droid's LED eye animation, colour, or pattern.",
}

_NOT_A_REQUEST = (
    "The speaker is chatting, commenting, reminiscing, asking about something that already "
    "happened, or describing something they might want later. Anything that is not an "
    "instruction to do this right now."
)


def _noul(instructions: str, when_true: str, when_false: str) -> Dict[str, Any]:
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": when_true, "false": when_false},
    }


TOOL_NOULS: Dict[str, Dict[str, Any]] = {
    "play_music": _noul(
        "Is the speaker telling the assistant to start playing music right now?",
        "They ask for music, songs, tunes, a mix, a playlist, or one named song or artist, to "
        'be started now. Slang for it counts: "spin something up", "start the jams", "put '
        'something on".',
        f"{_NOT_A_REQUEST} Asking to skip to a different song is not this.",
    ),
    "stop_music": _noul(
        "Is the speaker telling the assistant to stop or pause the music right now?",
        "They ask for the music, song, or sound to stop, pause, end, cut out, or go quiet now, "
        'including "knock it off", "enough", "silence", "quiet please".',
        _NOT_A_REQUEST,
    ),
    "next_track": _noul(
        "Is the speaker telling the assistant to skip the current track and play the next one?",
        "They ask to skip, advance, or change to the next song, or say they dislike this one "
        "and want a different one.",
        f"{_NOT_A_REQUEST} Asking to start music when none is playing is not this.",
    ),
    "dj_mode_on": _noul(
        "Is the speaker telling the assistant to enter DJ mode?",
        "They name DJ mode, or ask the droid to act as a DJ, host, or MC over the music, or to "
        '"do your DJ thing".',
        f"{_NOT_A_REQUEST} Asking merely to play music is not this.",
    ),
    "dj_mode_off": _noul(
        "Is the speaker telling the assistant to leave DJ mode?",
        "They ask to exit, end, or turn off DJ mode, or to stop acting as a DJ.",
        f"{_NOT_A_REQUEST} Asking to stop the music is not this.",
    ),
    "set_eye_animation": _noul(
        "Is the speaker telling the assistant to change its LED eye colour, pattern, or "
        "animation?",
        "They name the eyes, or a colour, flash, blink, scan, pulse, or rainbow effect the "
        "droid should display.",
        f"{_NOT_A_REQUEST} Asking about room lights or a screen is not this.",
    ),
}

#: The command/conversation gate. Not a tool — a veto.
IS_A_COMMAND_NOUL: Dict[str, Any] = _noul(
    "Is the speaker telling the assistant to do something, as opposed to talking with it?",
    "The utterance is an instruction or a request for an action.",
    "The utterance is conversation: a greeting, a compliment, a joke request, an opinion "
    "question, small talk, a question about something the assistant already did, or a fragment "
    "that asks for nothing.",
)

INTENT_CHOICE: Dict[str, Any] = {
    "type": "choice",
    "instructions": "Which one thing is the speaker asking the assistant to do right now?",
    "criteria": {
        **TOOL_DESCRIPTIONS,
        "general_chat": (
            "Nothing to do: the speaker is chatting, greeting, complimenting, joking, asking an "
            "opinion or a general-knowledge question, commenting on something already "
            "happening, or describing something they may want at some later time."
        ),
        "unclear": (
            "An action is wanted but the utterance does not say which: it is cut off, garbled, "
            "only a wake word, or too vague to match any of the tools above."
        ),
    },
}


def build_questions() -> Dict[str, Dict[str, Any]]:
    """The full question set sent on every utterance — 8 questions, ~1.2K input tokens.

    Jev answers them in parallel inside one request, so the extra questions cost tokens but
    almost no latency (the benchmark's sweep measured 184 ms for 3 questions and 228 ms for 12).
    """
    return {
        "intent": INTENT_CHOICE,
        **TOOL_NOULS,
        "is_a_command": IS_A_COMMAND_NOUL,
    }


def build_state(utterance: str) -> Dict[str, Any]:
    """The text Jev reasons over: the assistant's identity, and the utterance. Nothing else.

    The identity line is load-bearing — the benchmark found it is what fixes Star Wars
    vocabulary like *"put on some cantina tunes"*, which a bare transcript classifies as
    ``general_chat``.

    Conversation history is deliberately **excluded**. It raised strict accuracy by ~4 points,
    but it also made the router eager and reintroduced a false trigger: with history,
    *"did you turn the music down"* pushed its ``volume_down`` noul above 0.5 and started
    executing. Adding any further context here is a change that must be re-measured against
    the false-trigger set, not a free win.
    """
    return {
        "assistant": (
            "You are DJ R3X, a Star Wars droid DJ in a cantina. You play music, run a DJ mode "
            "where you talk over the tracks, and you have LED eyes you can change."
        ),
        "utterance": utterance,
    }


# --------------------------------------------------------------------------------------------
# Parameter extraction
# --------------------------------------------------------------------------------------------

#: Colour words ``EyeLightControllerService`` understands, longest-first so "light blue" beats
#: "blue" when both appear.
EYE_COLORS: List[str] = [
    "rainbow", "magenta", "purple", "orange", "yellow", "silver", "violet",
    "maroon", "indigo", "golden", "green", "white", "black", "amber", "cyan",
    "teal", "blue", "pink", "gold", "red",
]

#: Pattern words, mapped onto the ``EyePattern`` enum values the eye service accepts.
EYE_PATTERNS: Dict[str, str] = {
    "solid": "solid",
    "flash": "flash",
    "flashing": "flash",
    "blink": "flash",
    "blinking": "flash",
    "pulse": "flash",
    "pulsing": "flash",
    "happy": "happy",
    "sad": "sad",
    "angry": "angry",
    "surprised": "surprised",
    "idle": "idle",
    "thinking": "thinking",
}


def _word_search(text: str, words: List[str]) -> Optional[str]:
    """First whole-word match from ``words``, in the order given."""
    lowered = text.lower()
    for word in words:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return word
    return None


def extract_parameters(intent: str, utterance: str) -> Optional[Dict[str, Any]]:
    """Build the ``parameters`` dict ``IntentRouterService`` expects for ``intent``.

    Returns ``None`` when the utterance does not carry a parameter the handler requires — the
    caller then declines to fast-path and lets Claude handle the turn, which is the right
    outcome for something like "change your eyes" with no colour named.
    """
    if intent == "play_music":
        # FIXED 2026-09-17: this used to pass the whole utterance through as ``track``,
        # producing params={'track': 'Yeah. Go ahead and play some music for me.'} live. A
        # sentence is not a track name, and the layer below did not "sort it out" - it turned
        # it into `cantina_band`, which matches no file. ``track`` is now None unless the
        # speaker named a track, artist or mood. A generic request still dispatches: playing
        # *something* is exactly what was asked for.
        return {"track": naming_phrase(utterance)}

    if intent in ("stop_music", "next_track", "dj_mode_on", "dj_mode_off"):
        return {}

    if intent == "set_eye_animation":
        color = _word_search(utterance, EYE_COLORS)
        if not color:
            # ``_handle_set_eye_color_intent`` fails without a colour, so decline rather than
            # dispatch a command we know will not work.
            return None
        pattern_word = _word_search(utterance, list(EYE_PATTERNS.keys()))
        pattern = EYE_PATTERNS.get(pattern_word or "", "solid")
        return {"color": color, "pattern": pattern}

    return None


# --------------------------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------------------------

#: Firing threshold for the "cheap" tier — reversible but noticeable actions. The brief and the
#: benchmark agree on 0.85; ``JEV_CONFIDENCE_THRESHOLD`` overrides it.
DEFAULT_THRESHOLD = 0.85

#: Minimum for the tool's own noul in the cheap tier, on top of the choice confidence.
DEFAULT_CHEAP_NOUL = 0.7

#: The "free" tier fires more eagerly. Per the confidence docs, one threshold is the wrong
#: model: a wrong eye colour costs nothing, a wrongly-stopped track interrupts the room.
#: Derived as ``threshold - FREE_TIER_RELAXATION`` so that one config knob moves both tiers
#: together — an operator who tightens the gate must be able to tighten all of it.
FREE_TIER_RELAXATION = 0.10
DEFAULT_FREE_THRESHOLD = DEFAULT_THRESHOLD - FREE_TIER_RELAXATION  # 0.75

#: Minimum noul in the free tier — just enough to require the two reads to agree at all.
DEFAULT_FREE_NOUL = 0.5

#: The command/conversation gate is a veto, not a ranking, so it sits at the coin-flip line —
#: the benchmark used 0.5 and it produced no false triggers at that setting.
DEFAULT_COMMAND_THRESHOLD = 0.5

#: Instantly reversible, nothing destroyed. Fire eagerly.
FREE_TIER = frozenset({"set_eye_animation", "next_track"})

#: Reversible but noticeable in the room. R3X can apologise in character, but it is disruptive.
CHEAP_TIER = frozenset({"play_music", "stop_music", "dj_mode_on", "dj_mode_off"})

#: Writes to shared state; never fired from Jev alone. R3X has no such tool today, so this is
#: an empty guard rail rather than a live rule — it exists so that adding one (a calendar
#: entry, a Spotify playlist edit) does not silently inherit the cheap tier's gate.
COMMITTING_TIER: frozenset = frozenset()


def tier_gate(
    intent: str,
    threshold: float = DEFAULT_THRESHOLD,
    command_threshold: float = DEFAULT_COMMAND_THRESHOLD,
) -> Optional[Dict[str, float]]:
    """Gates for ``intent``, or ``None`` if it must never fire from the router alone.

    ``threshold`` is the cheap-tier confidence gate; the free tier is derived from it so a
    single config knob still moves both consistently.
    """
    if intent in COMMITTING_TIER:
        return None
    if intent in FREE_TIER:
        return {
            "confidence": threshold - FREE_TIER_RELAXATION,
            "noul": DEFAULT_FREE_NOUL,
            "command": command_threshold,
        }
    return {
        "confidence": threshold,
        "noul": DEFAULT_CHEAP_NOUL,
        "command": command_threshold,
    }


@dataclass
class RouterDecision:
    """What the router concluded, and enough of why to make a log line worth reading."""

    #: The winning tool name, or ``None`` when nothing should be dispatched.
    intent: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    #: ``min(choice confidence, own noul)`` — the single number worth logging.
    confidence: float = 0.0
    #: The Choice answer, including ``general_chat`` / ``unclear``. Useful for logs even when
    #: nothing fires.
    choice: Optional[str] = None
    #: The raw choice confidence and the winning tool's own noul, kept apart for the eval log.
    choice_confidence: float = 0.0
    noul: float = 0.0
    is_command: float = 0.0
    #: Which tier's gate was applied: "free", "cheap", "committing", or "" when not a tool.
    tier: str = ""
    #: Full Choice distribution. The report is explicit that this is the eval set — log it on
    #: every decline so criteria wording can be sharpened later.
    probabilities: Dict[str, float] = field(default_factory=dict)
    #: Human-readable reason, always set.
    reason: str = ""

    @property
    def should_execute(self) -> bool:
        return self.intent is not None

    @property
    def top_two(self) -> List[str]:
        """Top two Choice options as ``name (0.62)`` strings — the hint for Claude on a
        mid-confidence decline, per the report's 0.5-0.85 band guidance."""
        ranked = sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)
        return [f"{name} ({prob:.2f})" for name, prob in ranked[:2]]


def decide(
    result: Any,
    utterance: str,
    threshold: float = DEFAULT_THRESHOLD,
    command_threshold: float = DEFAULT_COMMAND_THRESHOLD,
) -> RouterDecision:
    """Turn a :class:`~cantina_os.llm.jev_client.JevResult` into a dispatch decision.

    ``result`` is duck-typed rather than imported so the unit tests can feed it recorded
    response objects without constructing a client.

    A tool fires only when every one of these holds:

    * the Choice picked that tool (not ``general_chat`` / ``unclear``);
    * the tool is not in :data:`COMMITTING_TIER`;
    * ``is_a_command >= command_threshold`` — it was an instruction, not conversation;
    * the choice confidence and the tool's own noul both clear that tool's **tier** gate;
    * the parameters the handler needs could be extracted from the utterance.

    Anything else returns a non-executing decision and the turn goes to Claude with the raw
    transcript and no action context.

    Note on stability: Jev is stable but not deterministic — the benchmark saw probability
    deltas up to 0.21 between identical passes, so an utterance sitting within ~0.1 of a gate
    will flip between runs. That is expected and safe (a flip costs one slow turn), but it is
    why these gates sit well above the 0.5 coin-flip line rather than just above it.
    """
    if result is None:
        return RouterDecision(reason="no Jev result (classifier unavailable)")

    answers = getattr(result, "answers", {}) or {}
    intent_answer = answers.get("intent")
    choice = getattr(intent_answer, "choice", None) if intent_answer else None
    choice_confidence = (
        float(getattr(intent_answer, "confidence", 0.0) or 0.0) if intent_answer else 0.0
    )
    probabilities = dict(getattr(intent_answer, "probabilities", {}) or {}) if intent_answer else {}
    is_command = result.noul("is_a_command", 1.0)

    def declined(reason: str, **overrides: Any) -> RouterDecision:
        base: Dict[str, Any] = dict(
            choice=choice,
            choice_confidence=choice_confidence,
            is_command=is_command,
            probabilities=probabilities,
            reason=reason,
        )
        base.update(overrides)
        return RouterDecision(**base)

    if not choice:
        return declined("Jev returned no intent choice")

    if choice in NO_TOOL_INTENTS:
        return declined(f"classified as {choice}; no action", confidence=choice_confidence)

    if choice not in TOOL_INTENTS:
        return declined(f"unknown intent '{choice}'; no action", confidence=choice_confidence)

    gates = tier_gate(choice, threshold=threshold, command_threshold=command_threshold)
    if gates is None:
        return declined(
            f"'{choice}' is a committing action; never fired from the router alone",
            tier="committing",
        )
    tier = "free" if choice in FREE_TIER else "cheap"

    own_noul = result.noul(choice, 0.0)
    confidence = min(choice_confidence, own_noul)

    # Gate 1: was this an instruction at all? Defaults to 1.0 when unanswered so a missing
    # question cannot silently veto everything. This is the gate that stops "did you turn the
    # music down" from turning the music down.
    if is_command < gates["command"]:
        return declined(
            f"'{choice}' vetoed by command gate (is_a_command={is_command:.2f} < "
            f"{gates['command']:.2f}); conversation, not an order",
            confidence=confidence,
            noul=own_noul,
            tier=tier,
        )

    # Gate 2: the two independent reads must agree, each against its tier's bar. The Choice is
    # a competition between options; the noul is an absolute yes/no about this one. They fail
    # independently, which is exactly what buys the zero-false-trigger property.
    if choice_confidence < gates["confidence"] or own_noul < gates["noul"]:
        return declined(
            f"'{choice}' below {tier}-tier gate (choice={choice_confidence:.2f} vs "
            f"{gates['confidence']:.2f}, noul={own_noul:.2f} vs {gates['noul']:.2f})",
            confidence=confidence,
            noul=own_noul,
            tier=tier,
        )

    # Gate 3: can we actually build a dispatchable command?
    parameters = extract_parameters(choice, utterance)
    if parameters is None:
        return declined(
            f"'{choice}' cleared its gate at {confidence:.2f} but required parameters are "
            "missing from the utterance",
            confidence=confidence,
            noul=own_noul,
            tier=tier,
        )

    return RouterDecision(
        intent=choice,
        parameters=parameters,
        confidence=confidence,
        choice=choice,
        choice_confidence=choice_confidence,
        noul=own_noul,
        is_command=is_command,
        tier=tier,
        probabilities=probabilities,
        reason=(
            f"'{choice}' cleared the {tier}-tier gate "
            f"(choice={choice_confidence:.2f}, noul={own_noul:.2f})"
        ),
    )
