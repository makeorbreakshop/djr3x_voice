"""R3X show system: gesture library, cues and sequences (``show/SPEC.md``).

* ``models``   - Pydantic models for clip / cue / sequence;
* ``loader``   - load the repo-root ``show/`` folder (tolerant of missing/empty folders);
* ``validate`` - cross-file rules, including tiers;
* ``expand``   - flatten to timed department actions (the cross-language parity contract);
* ``catalog``  - the compact catalogue Claude sees in its system prompt;
* ``tags``     - inline ``{cue:id}`` / ``{clip:id}`` tags: stream stripping and speech timing.

The live player is the Rust r3x performer (``r3x-runtime --bridge``); CantinaOS only requests shows.
"""

from .expand import expand
from .loader import ShowLibrary, ShowLibraryHandle, load_library, resolve_show_dir
from .models import TIER_RANK, Clip, Cue, Sequence, parse_item

__all__ = [
    "Clip", "Cue", "Sequence", "ShowLibrary", "ShowLibraryHandle", "TIER_RANK",
    "expand", "load_library", "parse_item", "resolve_show_dir",
]
