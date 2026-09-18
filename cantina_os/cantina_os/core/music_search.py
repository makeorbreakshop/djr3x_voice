"""Small, transport-safe types shared by Jev and the music controller."""

import re
from dataclasses import dataclass

SEMANTIC_PREFIX = "@semantic"
AVOID_SEPARATOR = "@avoid"


@dataclass(frozen=True)
class SemanticMusicRequest:
    query: str
    negative_query: str | None = None


def encode_semantic_request(query: str, negative_query: str | None = None) -> str:
    """Encode a structured request so it survives the existing CLI command bus."""
    positive = " ".join((query or "").split()).strip()
    if not positive:
        raise ValueError("semantic music query cannot be empty")
    encoded = f"{SEMANTIC_PREFIX} {positive}"
    negative = " ".join((negative_query or "").split()).strip()
    if negative:
        encoded += f" {AVOID_SEPARATOR} {negative}"
    return encoded


def parse_semantic_request(value: str) -> SemanticMusicRequest | None:
    """Decode a request produced by :func:`encode_semantic_request`."""
    text = " ".join((value or "").split()).strip()
    if not text.lower().startswith(f"{SEMANTIC_PREFIX} "):
        return None
    body = text[len(SEMANTIC_PREFIX) :].strip()
    parts = re.split(
        rf"\s+{re.escape(AVOID_SEPARATOR)}\s+", body, maxsplit=1, flags=re.IGNORECASE
    )
    query = parts[0].strip()
    if not query:
        return None
    negative = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
    return SemanticMusicRequest(query=query, negative_query=negative)


SEMANTIC_MUSIC_WORDS = frozenset(
    {
        "aggressive",
        "ambient",
        "angry",
        "atmospheric",
        "bright",
        "calm",
        "celebratory",
        "cheerful",
        "chill",
        "cinematic",
        "country",
        "dance",
        "dark",
        "dramatic",
        "dreamy",
        "electronic",
        "energetic",
        "energy",
        "epic",
        "funk",
        "funky",
        "fun",
        "gentle",
        "happy",
        "hard",
        "heavy",
        "hopeful",
        "intense",
        "jazz",
        "lively",
        "melancholy",
        "mellow",
        "metal",
        "optimistic",
        "party",
        "peaceful",
        "playful",
        "quirky",
        "relaxing",
        "robotic",
        "rock",
        "sad",
        "soft",
        "space",
        "strange",
        "synth",
        "uplifting",
        "upbeat",
    }
)


def looks_like_semantic_music_request(value: str) -> bool:
    """Conservative fallback for typed/Claude requests that did not pass through Jev."""
    words = set(re.findall(r"[a-z0-9']+", (value or "").lower()))
    return bool(words & SEMANTIC_MUSIC_WORDS)
