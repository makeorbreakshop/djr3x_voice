"""
What, in a spoken sentence, actually names a track.

Two layers need this answer and they must agree:

* ``llm.jev_intents.extract_parameters`` decides what ``track`` to put on ``INTENT_DETECTED``;
* ``services.intent_router_service.IntentRouterService._select_smart_track`` decides what to
  put on the ``play music`` command.

They disagreed on 2026-09-17. The router's own alias table mapped anything generic to
``cantina_band``, a name matching no file, so "Yeah. Go ahead and play some music for me."
became a track request and R3X announced a track that was not playing. Living in one module
is what stops that recurring: a request either names something, or it does not, and both
layers get the same verdict.

The rule is deliberately conservative. Anything not on the filler list counts as naming
something, and is handed on to ``MusicControllerService._smart_play_track`` — the only matcher
in the system that knows the real library. A false "this names something" costs one fuzzy
match against 21 titles; a false "this is generic" would ignore a request the user made.
"""

import re
from typing import Optional, Set

#: Words that carry no information about *which* track: verbs of playing, politeness,
#: pronouns, and the generic nouns for music itself. A request made only of these is generic,
#: and the honest answer to "which track?" is "the controller picks".
GENERIC_REQUEST_WORDS: Set[str] = {
    "a",
    "ahead",
    "along",
    "an",
    "and",
    "any",
    "anything",
    "beat",
    "beats",
    "can",
    "could",
    "do",
    "for",
    "go",
    "going",
    "have",
    "hear",
    "hey",
    "i",
    "in",
    "it",
    "jam",
    "jams",
    "just",
    "let",
    "lets",
    "let's",
    "like",
    "listen",
    "me",
    "music",
    "my",
    "now",
    "of",
    "ok",
    "okay",
    "on",
    "one",
    "play",
    "playing",
    "please",
    "put",
    "r3x",
    "rex",
    "s",
    "shuffle",
    "some",
    "something",
    "song",
    "songs",
    "sound",
    "sounds",
    "spin",
    "start",
    "the",
    "thing",
    "to",
    "track",
    "tracks",
    "tune",
    "tunes",
    "up",
    "us",
    "want",
    "we",
    "would",
    "yeah",
    "yes",
    "you",
}


def naming_phrase(request: Optional[str]) -> Optional[str]:
    """Reduce a spoken music request to the words that identify a track.

    Args:
        request: What the speaker said, or the slice of it that was captured.

    Returns:
        A bare track number as given; or the distinguishing words, space-joined and
        lowercased; or ``None`` when the request names nothing at all.

    >>> naming_phrase("Yeah. Go ahead and play some music for me.") is None
    True
    >>> naming_phrase("put on Huttuk Cheeka")
    'huttuk cheeka'
    >>> naming_phrase("play track 4")
    '4'
    """
    text = (request or "").strip()
    if not text:
        return None

    # An explicit track number passes through untouched: MusicControllerService indexes on it.
    if text.isdigit():
        return text

    words = re.findall(r"[a-z0-9']+", text.lower())
    meaningful = [w for w in words if w not in GENERIC_REQUEST_WORDS]

    if not meaningful:
        return None

    return " ".join(meaningful)
