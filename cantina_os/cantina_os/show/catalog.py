"""The compact show catalogue Claude sees in its system prompt.

Built once at startup from the show files. It is appended to the *cached* system prompt, so
it must be byte-stable across turns: items are sorted by (kind, id), and nothing
time-varying goes in. Content added while CantinaOS runs reaches the timeline (and the CLI)
immediately, but Claude's catalogue only on the next restart - that is the price of the
cache hit.

* **Tags** may name ``free`` and ``cheap`` clips and cues.
* The ``perform_show`` tool may name ``cheap`` and ``show`` sequences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Tuple

TAGGABLE_TIERS = ("free", "cheap")
TOOL_TIERS = ("cheap", "show")

TAG_GUIDANCE = """\
You have a body and lights. You can punctuate what you say with an inline tag written
immediately before the word it goes with: {cue:<id>} for a whole moment (motion, eyes,
chest, lights, sound) or {clip:<id>} for a single gesture. Tags are silent - they are
removed before your words are spoken - and each fires as your voice reaches it.
Use them sparingly: none is often right, never more than two in a reply, and only when
the move adds something the words do not. Only use ids from the lists below.
"""
TOOL_GUIDANCE = """\
For a full routine ("do your intro", "malfunction!"), call the perform_show tool with a
routine id from <routines>; keep talking as normal, the routine runs alongside you.
"""


@dataclass(frozen=True)
class ShowCatalog:
    taggable: FrozenSet[Tuple[str, str]] = frozenset()
    tool_ids: Tuple[str, ...] = ()
    prompt_block: str = ""

    @property
    def empty(self) -> bool:
        return not self.taggable and not self.tool_ids


def _line(item) -> str:
    desc = " ".join((item.description or item.title or "").split())
    return f"- {item.id}: {desc}" if desc else f"- {item.id}"


def build_catalog(library) -> ShowCatalog:
    """Catalogue of every *valid* performable item, in a stable order."""
    ok = [i for i in library.items.values() if library.is_valid(i.id)]
    clips = sorted((i for i in ok if i.kind == "clip" and i.tier in TAGGABLE_TIERS), key=lambda i: i.id)
    cues = sorted((i for i in ok if i.kind == "cue" and i.tier in TAGGABLE_TIERS), key=lambda i: i.id)
    seqs = sorted((i for i in ok if i.kind == "sequence" and i.tier in TOOL_TIERS), key=lambda i: i.id)
    if not (clips or cues or seqs):
        return ShowCatalog()

    guidance = ""
    if clips or cues:
        first = (["{clip:%s} You got it, friend." % clips[0].id] if clips else []) + (
            ["Now {cue:%s} let's MOVE!" % cues[0].id] if cues else [])
        guidance += TAG_GUIDANCE + 'Example: "' + " ".join(first) + '"\n'
    if seqs:
        guidance += TOOL_GUIDANCE
    parts: List[str] = ["<performance>\n" + guidance + "</performance>"]
    if cues:
        parts.append("<cues>\n" + "\n".join(_line(i) for i in cues) + "\n</cues>")
    if clips:
        parts.append("<clips>\n" + "\n".join(_line(i) for i in clips) + "\n</clips>")
    if seqs:
        parts.append("<routines>  (perform_show tool only)\n" + "\n".join(_line(i) for i in seqs) + "\n</routines>")
    return ShowCatalog(
        taggable=frozenset({("clip", i.id) for i in clips} | {("cue", i.id) for i in cues}),
        tool_ids=tuple(i.id for i in seqs),
        prompt_block="\n".join(parts),
    )
