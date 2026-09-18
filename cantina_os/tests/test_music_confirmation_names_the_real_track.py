"""
R3X must confirm the track that actually started playing.

## The defect this pins (observed live 2026-09-17 10:57:24)

"Yeah. Go ahead and play some music for me." produced this chain:

    IntentRouterService  "Smart track selection: '<the whole utterance>' -> 'cantina_band'"
    MusicController      "No matches found for 'cantina_band', playing first track"
    MusicController      "Playing track: Huttuk Cheeka"
    Claude               "Now spinning up "Cantina Band" for you."

Three separate faults in one line:

1. `_select_smart_track`'s alias table maps to invented ids (`cantina_band`,
   `imperial_march`, ...) that are not in the library. The real file is
   "Cantina Song aka Mad About Mad About Me".
2. A generic request ("some music") should carry **no** track and let
   MusicControllerService choose, rather than being forced to a name that cannot match.
3. The execution result reported the *requested* track, not the one that started, so the
   spoken confirmation named a track the user was not hearing.

## What is real here

* the real `IntentRouterService`, and
* the real `MusicControllerService._smart_play_track` matcher, driven over the **real**
  21-track library, with only `_play_track_by_name` recorded instead of driving VLC.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.core.fast_router_gate import GATE, ActionTaken
from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.services.music_controller_service.music_controller_service import (
    MusicControllerService,
)

#: The library as `MusicControllerService._load_music_library` actually keys it: the parsed
#: *title* of every file in `audio/music/`, which is why "Elem Zadowz - Huttuk Cheeka.mp3"
#: appears as "Huttuk Cheeka". Duplicate titles are disambiguated with artist-qualified keys,
#: so both "Utinni.mp3" and "The Dusty Jawas - Utinni.mp3" remain playable.
REAL_LIBRARY = [
    "Bai Tee Tee",
    "Batuu Boogie",
    "Cantina Song aka Mad About Mad About Me",
    "Doolstan",
    "Droid World",
    "Beep Boop Bop",
    "Huttuk Cheeka",
    "Oola Shuka",
    "Gaya",
    "Una Duey Dee",
    "Nama Heh",
    "Modal Notes",
    "Moulee-rah",
    "Bright Suns",
    "Doshka",
    "Turbulence",
    "Opening",
    "Utinni",
    "The Dusty Jawas - Utinni",
    "Aloogahoo",
    "Yocola Ateema",
    "Goola Bukee",
]

THE_UTTERANCE = "Yeah. Go ahead and play some music for me."


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


def test_the_real_library_has_22_distinct_keys():
    """Guards the fixture itself: if the library changes, the tests below must be revisited."""
    assert len(REAL_LIBRARY) == 22
    assert len(set(REAL_LIBRARY)) == 22


# ---------------------------------------------------------------------------------------
# 1. The selector
# ---------------------------------------------------------------------------------------

@pytest.fixture
def router():
    svc = IntentRouterService(AsyncIOEventEmitter())
    svc.emit = AsyncMock()  # type: ignore[assignment]
    return svc


class TestSmartTrackSelection:
    async def test_generic_request_selects_no_track(self, router):
        """"play some music" names nothing, so nothing must be named downstream."""
        assert await router._select_smart_track(THE_UTTERANCE) is None

    @pytest.mark.parametrize(
        "request_text",
        ["some music", "music", "play a song", "anything", "put on some tunes"],
    )
    async def test_other_generic_phrasings_select_no_track(self, router, request_text):
        assert await router._select_smart_track(request_text) is None

    async def test_named_request_keeps_the_distinguishing_words(self, router):
        """A real request must survive to MusicControllerService, which owns the real matcher."""
        selected = await router._select_smart_track("play the cantina song")
        assert selected is not None
        assert "cantina" in selected.lower()

    async def test_selector_never_invents_a_track_id(self, router):
        """The old alias table produced ids no file could match."""
        for phrase in ["cantina music", "some imperial march", "droid tunes", "jedi rocks"]:
            selected = await router._select_smart_track(phrase)
            assert selected not in {
                "cantina_band",
                "imperial_march",
                "droid_march",
                "jedi_rocks",
            }, f"{phrase!r} still resolves to an invented id: {selected!r}"

    async def test_selector_does_not_list_the_library_as_a_side_effect(self, router):
        """The old implementation emitted `list music` on every selection, which dumped the
        whole track list to the CLI mid-turn (observed at 10:57:24.332)."""
        await router._select_smart_track(THE_UTTERANCE)
        emitted = [c.args[1] for c in router.emit.await_args_list if len(c.args) > 1]
        assert not any(
            (e or {}).get("command") == "list" for e in emitted
        ), f"selection still emits a list command: {emitted}"


# ---------------------------------------------------------------------------------------
# 2. The real matcher over the real library
# ---------------------------------------------------------------------------------------

@pytest.fixture
def matcher():
    """The real `_smart_play_track`, over the real library, without VLC."""
    svc = MusicControllerService.__new__(MusicControllerService)
    svc._service_name = "music_controller_test"
    svc._logger = MagicMock()
    svc.emit = AsyncMock()
    svc._send_success = AsyncMock()
    svc._send_error = AsyncMock()
    svc.backends = {}
    svc.tracks = {title: MagicMock(name=title) for title in REAL_LIBRARY}
    svc.played: List[str] = []

    async def _play(track_name: str, source: str = "cli") -> None:
        svc.played.append(track_name)

    svc._play_track_by_name = _play  # type: ignore[assignment]
    return svc


class TestRealMatcher:
    async def test_cantina_request_reaches_the_real_cantina_file(self, matcher, router):
        """End of the selection chain: "the cantina song" must reach the file that exists."""
        selected = await router._select_smart_track("play the cantina song")
        await matcher._smart_play_track(selected or "")
        assert matcher.played == ["Cantina Song aka Mad About Mad About Me"]

    async def test_an_unknown_name_never_plays_the_unrelated_first_track(self, matcher):
        """A failed lookup must be honest when no remote catalog is available."""
        await matcher._smart_play_track("cantina_band")
        assert matcher.played == []
        matcher._send_error.assert_awaited_once_with(
            "No music found matching 'cantina_band'"
        )


# ---------------------------------------------------------------------------------------
# 3. The execution result and the action block must name what started
# ---------------------------------------------------------------------------------------

class Probe:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def handler(self, payload: Any) -> None:
        self.events.append(payload if isinstance(payload, dict) else payload.model_dump())


class PlaybackStub:
    """Stands in for MusicControllerService: answers a play command with what it started.

    Mirrors production's behaviour for a generic request - the controller, not the router,
    picks the track - which is exactly the case where the two names diverged live.
    """

    def __init__(self, bus: AsyncIOEventEmitter, starts: str = "Huttuk Cheeka") -> None:
        self._bus = bus
        self._starts = starts
        self.commands: List[Dict[str, Any]] = []
        bus.on(EventTopics.CLI_COMMAND.value, self._on_command)

    def _on_command(self, payload: Dict[str, Any]) -> None:
        if payload.get("command") != "play":
            return
        self.commands.append(payload)
        self._bus.emit(
            EventTopics.MUSIC_PLAYBACK_STARTED.value,
            {"track": {"title": self._starts, "name": self._starts}, "source": "cli"},
        )


@pytest.fixture
async def live_router():
    GATE.reset()
    bus = AsyncIOEventEmitter()
    results = Probe()
    bus.on(EventTopics.INTENT_EXECUTION_RESULT.value, results.handler)
    svc = IntentRouterService(bus)
    await svc.start()
    playback = PlaybackStub(bus)
    await asyncio.sleep(0.1)
    yield {"bus": bus, "router": svc, "results": results, "playback": playback}
    try:
        await svc.stop()
    except Exception:
        pass
    GATE.reset()


def _fast_routed_play(bus: AsyncIOEventEmitter, track: Optional[str] = None) -> None:
    """What JevIntentService publishes for a generic "play some music"."""
    GATE.register_router()
    GATE.resolve(
        THE_UTTERANCE,
        ActionTaken(
            intent_name="play_music",
            parameters={"track": track},
            confidence=0.97,
            transcript=THE_UTTERANCE,
            at=time.time(),
        ),
    )
    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": "play_music",
            "parameters": {"track": track},
            "confidence": 0.97,
            "original_text": THE_UTTERANCE,
            "conversation_id": "turn-1",
            "source": "jev_fast_router",
        },
    )


class TestExecutionResult:
    async def test_generic_request_dispatches_play_with_no_track_argument(self, live_router):
        _fast_routed_play(live_router["bus"])
        await asyncio.sleep(1.2)
        commands = live_router["playback"].commands
        assert len(commands) == 1
        assert commands[0].get("args") == [], (
            f"a generic request must name no track, got args={commands[0].get('args')!r}"
        )

    async def test_result_carries_the_track_that_actually_started(self, live_router):
        _fast_routed_play(live_router["bus"])
        await asyncio.sleep(1.2)

        events = live_router["results"].events
        assert len(events) == 1, f"expected one execution result, got {len(events)}"
        result = events[0]["result"]
        assert result["track"] == "Huttuk Cheeka", (
            f"execution result names {result.get('track')!r}, but Huttuk Cheeka is playing"
        )
        assert "Huttuk Cheeka" in result["message"]

    async def test_the_action_record_is_amended_with_the_started_track(self, live_router):
        """This is what `<action_already_taken>` is rendered from, so it has to be corrected
        too - otherwise Claude is told one track and shown another."""
        _fast_routed_play(live_router["bus"])
        await asyncio.sleep(1.2)

        record = GATE.peek(THE_UTTERANCE)
        assert record is not None
        assert record.parameters.get("track") == "Huttuk Cheeka", (
            f"gate record still says track={record.parameters.get('track')!r}"
        )

    async def test_the_utterance_never_becomes_a_track_name(self, live_router):
        """The literal regression: `params={'track': 'Yeah. Go ahead and play some music...'}`."""
        _fast_routed_play(live_router["bus"], track=THE_UTTERANCE)
        await asyncio.sleep(1.2)

        result = live_router["results"].events[0]["result"]
        assert THE_UTTERANCE not in str(result.get("track"))
        assert result["track"] == "Huttuk Cheeka"


# ---------------------------------------------------------------------------------------
# 4. What Claude is actually told
# ---------------------------------------------------------------------------------------

class StubAnthropicMessages:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Text:
            type = "text"
            text = "Huttuk Cheeka is SPINNING!"

        class _Usage:
            input_tokens = 100
            output_tokens = 8
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0

        class _Response:
            content = [_Text()]
            usage = _Usage()

        return _Response()


class StubAnthropic:
    def __init__(self) -> None:
        self.messages = StubAnthropicMessages()


@pytest.fixture
async def claude_rig(live_router):
    from cantina_os.services.claude_service.claude_service import ClaudeService

    bus = live_router["bus"]
    claude = ClaudeService(
        bus,
        {
            "ANTHROPIC_API_KEY": "test-key-not-used",
            "STREAMING": False,
            "FAST_ROUTER_WAIT_S": 2.0,
        },
    )
    await claude.start()
    stub = StubAnthropic()
    claude._client = stub  # type: ignore[assignment]
    await asyncio.sleep(0.2)
    yield {**live_router, "claude": claude, "stub": stub}
    await asyncio.sleep(0.2)
    try:
        await claude.stop()
    except Exception:
        pass


async def test_action_already_taken_block_names_the_started_track(claude_rig):
    """The block Claude reads must say Huttuk Cheeka, because that is what is audible."""
    bus = claude_rig["bus"]
    # Production order: the router claims the gate at startup, long before a transcript, so
    # ClaudeService is already waiting on verdicts when the turn begins.
    GATE.register_router()
    bus.emit(
        EventTopics.VOICE_LISTENING_STOPPED.value,
        {"transcript": THE_UTTERANCE, "conversation_id": "turn-1"},
    )
    await asyncio.sleep(0)
    _fast_routed_play(bus, track=THE_UTTERANCE)
    await asyncio.sleep(2.0)

    calls = claude_rig["stub"].messages.calls
    assert len(calls) == 1, f"expected one Anthropic request, got {len(calls)}"
    sent = str(calls[0]["messages"])
    assert "<action_already_taken>" in sent
    assert "Huttuk Cheeka" in sent, (
        "Claude was not told which track started; the block still reads: "
        f"{sent[sent.find('<action_already_taken>'):][:300]}"
    )
    block = sent[sent.index("<action_already_taken>"):sent.index("</action_already_taken>")]
    assert THE_UTTERANCE not in block, (
        f"the raw utterance is still presented as a track name: {block}"
    )
    assert "track='Huttuk Cheeka'" in block


# ---------------------------------------------------------------------------------------
# 5. "play music" with no track has to be a legal command
# ---------------------------------------------------------------------------------------

class TestGenericPlayCommand:
    """Found by `scripts/system_smoke_run.py`, not by any unit test.

    Once the router stopped inventing a track for a generic request, the bare `play music`
    command it emits was rejected by the CLI arg validator:

        dj-r3x> Error: Command 'play music' requires 1 arguments. Missing: track_name

    The action dispatched in 483 ms and was then thrown away - the same class of bug as the
    `eye pattern` arg-count rejection in Session 1. "Play some music" is a complete request,
    and so is typing `p` at the prompt, so the command has to accept zero arguments.
    """

    async def test_no_arguments_plays_something_from_the_real_library(self, matcher):
        # Exactly the payload IntentRouterService emits for a generic request.
        await matcher.handle_play_music(
            {"command": "play", "subcommand": "music", "args": [], "raw_input": "play music"}
        )

        assert len(matcher.played) == 1, (
            f"`play music` with no arguments played nothing; errors="
            f"{matcher._send_error.await_args_list}"
        )
        assert matcher.played[0] in REAL_LIBRARY

    async def test_a_named_track_still_works(self, matcher):
        await matcher.handle_play_music(
            {
                "command": "play",
                "subcommand": "music",
                "args": ["Turbulence"],
                "raw_input": "play music Turbulence",
            }
        )
        assert matcher.played == ["Turbulence"]
