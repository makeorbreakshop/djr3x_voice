"""Load the repo-root ``show/`` folder into a :class:`ShowLibrary`.

Tolerant by design: content is authored by hand (and, right now, by another agent in
parallel), so a missing folder, an empty folder, or one malformed file must never stop
CantinaOS. Every problem is recorded on ``library.issues``; items that fail to parse are
left out, items that parse but fail a cross-file rule stay in with an error recorded and
are refused at perform time.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError

from .models import MODEL_BY_KIND, ShowItem, parse_item

logger = logging.getLogger("cantina_os.show")

#: cantina_os/cantina_os/show/loader.py -> repo root is parents[3].
DEFAULT_SHOW_DIR = Path(__file__).resolve().parents[3] / "show"
FOLDERS = {"clip": "clips", "cue": "cues", "sequence": "sequences"}


def resolve_show_dir(configured: Optional[str] = None) -> Path:
    """Explicit config wins, then ``SHOW_DIR`` in the environment, then repo-root ``show/``."""
    path = configured or os.environ.get("SHOW_DIR")
    return Path(path).expanduser() if path else DEFAULT_SHOW_DIR


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    where: str  # item id or file path
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.where}: {self.message}"


@dataclass
class ShowLibrary:
    items: Dict[str, ShowItem] = field(default_factory=dict)
    issues: List[Issue] = field(default_factory=list)
    idle: Optional[dict] = None
    root: Optional[Path] = None
    #: Fingerprint of the files this was loaded from; see ``ShowLibraryHandle``.
    stamp: Tuple = ()

    # ------------------------------------------------------------------ queries
    def get(self, item_id: str) -> Optional[ShowItem]:
        return self.items.get(item_id)

    def of_kind(self, kind: str) -> List[ShowItem]:
        return sorted((i for i in self.items.values() if i.kind == kind), key=lambda i: i.id)

    def errors_for(self, item_id: str) -> List[Issue]:
        return [i for i in self.issues if i.where == item_id and i.severity == "error"]

    def is_valid(self, item_id: str) -> bool:
        return item_id in self.items and not self.errors_for(item_id)

    def __len__(self) -> int:
        return len(self.items)

    # ------------------------------------------------------------------ building
    @classmethod
    def from_items(cls, raw_items: Iterable[dict], validate: bool = True) -> "ShowLibrary":
        """Build a library from in-memory dicts (fixtures, tests)."""
        lib = cls()
        for raw in raw_items:
            where = raw.get("id", "?") if isinstance(raw, dict) else "?"
            lib._add(raw, where)
        if validate:
            from .validate import validate_library

            lib.issues.extend(validate_library(lib))
        return lib

    def _add(self, raw: dict, where: str, expect_kind: Optional[str] = None,
             expect_id: Optional[str] = None) -> None:
        try:
            item = parse_item(raw)
        except (ValidationError, ValueError) as e:
            self.issues.append(Issue("error", where, f"unparseable: {_short(e)}"))
            return
        if expect_kind and item.kind != expect_kind:
            self.issues.append(Issue("error", where, f"kind {item.kind!r} in the {FOLDERS[expect_kind]}/ folder"))
            return
        if expect_id and item.id != expect_id:
            self.issues.append(Issue("error", where, f"file name must equal the id ({item.id!r})"))
            return
        if item.id in self.items:
            self.issues.append(Issue("error", item.id, f"duplicate id (already a {self.items[item.id].kind})"))
            return
        self.items[item.id] = item


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'item'}: {err['msg']}" for err in e.errors()[:4]
        )
    return str(e)


def _files(root: Path) -> List[Tuple[str, Path]]:
    out = []
    for kind, folder in FOLDERS.items():
        d = root / folder
        if d.is_dir():
            out.extend((kind, p) for p in sorted(d.glob("*.json")))
    return out


def _stamp(root: Path) -> Tuple:
    entries = []
    for _kind, p in _files(root):
        try:
            st = p.stat()
            entries.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    idle = root / "idle.json"
    if idle.is_file():
        st = idle.stat()
        entries.append((str(idle), st.st_mtime_ns, st.st_size))
    return tuple(entries)


def load_library(path: Optional[os.PathLike] = None, validate: bool = True) -> ShowLibrary:
    """Load clips/, cues/, sequences/ and idle.json under ``path`` (default: resolve_show_dir())."""
    root = Path(path) if path else resolve_show_dir()
    lib = ShowLibrary(root=root)
    if not root.is_dir():
        logger.info(f"Show folder {root} does not exist; the show library is empty")
        return lib
    lib.stamp = _stamp(root)
    for kind, p in _files(root):
        try:
            raw = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as e:
            lib.issues.append(Issue("error", str(p), f"unreadable JSON: {e}"))
            continue
        lib._add(raw, str(p), expect_kind=kind, expect_id=p.stem)
    idle = root / "idle.json"
    if idle.is_file():
        try:
            lib.idle = json.loads(idle.read_text())
        except (OSError, json.JSONDecodeError) as e:
            lib.issues.append(Issue("error", str(idle), f"unreadable JSON: {e}"))
    if validate:
        from .validate import validate_library

        lib.issues.extend(validate_library(lib))
    for issue in lib.issues:
        (logger.warning if issue.severity == "error" else logger.debug)(f"show: {issue}")
    return lib


class ShowLibraryHandle:
    """A library that reloads itself when the files under its folder change.

    Cheap: one ``stat`` per file. Used by the timeline so content authored while CantinaOS
    runs becomes performable without a restart. (ClaudeService deliberately does NOT use
    this: its catalogue is part of the cached system prompt and must not churn.)
    """

    def __init__(self, path: Optional[os.PathLike] = None):
        self.root = Path(path) if path else resolve_show_dir()
        self._lib = load_library(self.root)

    @property
    def library(self) -> ShowLibrary:
        if self.root.is_dir() and _stamp(self.root) != self._lib.stamp:
            logger.info(f"Show folder {self.root} changed; reloading")
            self._lib = load_library(self.root)
        return self._lib

    def reload(self) -> ShowLibrary:
        self._lib = load_library(self.root)
        return self._lib

    def set(self, library: ShowLibrary) -> None:
        """Pin an in-memory library (tests); disables change detection."""
        self._lib = library
        self.root = Path("/nonexistent-show-dir-pinned")
