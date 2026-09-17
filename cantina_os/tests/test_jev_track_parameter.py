"""
The fast router's `track` parameter must name a track, or nothing at all.

## The defect this pins (observed live 2026-09-17 10:57:24.319)

    Jev dispatched 'play_music' in 0.7 ms params={'track': 'Yeah. Go ahead and play some music for me.'}

`extract_parameters` handed the whole utterance through as `track`, on the assumption that
`IntentRouterService._select_smart_track` would sort it out. It did not: the utterance went
into an alias table and came out as `cantina_band`, which matches no file. A sentence is not a
track name, and pretending otherwise pushed the problem one layer down where it got worse.

The contract: `track` is None for a generic request, and set only when the speaker actually
named a track, artist or mood.
"""

from unittest.mock import AsyncMock

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.llm.jev_intents import extract_parameters
from cantina_os.services.intent_router_service import IntentRouterService


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


class TestGenericRequestsCarryNoTrack:
    @pytest.mark.parametrize(
        "utterance",
        [
            "Yeah. Go ahead and play some music for me.",
            "play some music",
            "play music",
            "put on some tunes",
            "hey Rex, play a song",
            "spin some beats",
            "can you play something",
            "let's have some music",
            "music please",
            "start the music",
        ],
    )
    def test_no_track_is_named(self, utterance):
        params = extract_parameters("play_music", utterance)
        assert params is not None, "a generic request must still dispatch, just without a track"
        assert params["track"] is None, (
            f"{utterance!r} names no track, but track={params['track']!r}"
        )


class TestNamedRequestsCarryTheName:
    @pytest.mark.parametrize(
        "utterance, expected",
        [
            ("play the cantina song", "cantina"),
            ("put on Huttuk Cheeka", "huttuk cheeka"),
            ("play some Gaya", "gaya"),
            ("play something upbeat", "upbeat"),
            ("I want to hear Turbulence", "turbulence"),
            ("play track 4", "4"),
        ],
    )
    def test_the_naming_words_survive(self, utterance, expected):
        params = extract_parameters("play_music", utterance)
        assert params is not None
        track = params["track"]
        assert track is not None, f"{utterance!r} names a track, but track is None"
        assert expected in track.lower(), (
            f"{utterance!r} → track={track!r}, expected it to contain {expected!r}"
        )

    def test_the_whole_utterance_is_never_the_track(self):
        """The literal regression."""
        utterance = "Yeah. Go ahead and play some Huttuk Cheeka for me."
        params = extract_parameters("play_music", utterance)
        assert params is not None
        assert params["track"] != utterance
        assert "huttuk cheeka" in params["track"].lower()


class TestRouterAgreesWithTheRouterSelector:
    """One definition of "generic", shared by the two places that need it.

    If these drift, a request the router calls generic can still arrive with a track name
    attached, which is how the original bug survived review.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            "Yeah. Go ahead and play some music for me.",
            "play some music",
            "put on some tunes",
        ],
    )
    async def test_both_layers_call_the_same_things_generic(self, utterance):
        router = IntentRouterService(AsyncIOEventEmitter())
        router.emit = AsyncMock()  # type: ignore[assignment]

        assert extract_parameters("play_music", utterance)["track"] is None
        assert await router._select_smart_track(utterance) is None
