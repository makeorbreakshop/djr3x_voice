"""Show format v1 (show/SPEC.md): models, loader, validation, catalogue and golden parity.

The golden files in show/tests/golden/ are HAND-WRITTEN. If a parity test fails, fix the
implementation (or raise the disagreement) - never regenerate a golden file.
"""

import json
from pathlib import Path

import pytest

from cantina_os.show import ShowLibrary, expand, load_library
from cantina_os.show.catalog import build_catalog
from cantina_os.show.loader import DEFAULT_SHOW_DIR, ShowLibraryHandle, resolve_show_dir

SHOW_TESTS = DEFAULT_SHOW_DIR / "tests"
FIXTURES = sorted((SHOW_TESTS / "fixtures").glob("*.json"))


def test_there_are_fixtures_to_check():
    assert FIXTURES, f"no parity fixtures under {SHOW_TESTS}"


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_golden_parity(fixture_path: Path):
    fixture = json.loads(fixture_path.read_text())
    golden_path = SHOW_TESTS / "golden" / fixture_path.name
    assert golden_path.exists(), f"missing golden file {golden_path}"
    lib = ShowLibrary.from_items(fixture["items"])
    assert [str(i) for i in lib.issues if i.severity == "error"] == []
    got = expand(lib, fixture["fixture"]["root"], bpm=fixture["fixture"].get("bpm"))
    # Parsed JSON comparison, so 2 == 2.0.
    assert got == json.loads(golden_path.read_text())


# ---------------------------------------------------------------------------- helpers

def clip(id, tier="free", **kw):
    return {"id": id, "kind": "clip", "tier": tier, "description": f"{id} clip", "duration": 1.0,
            "tracks": {"head_tilt": {"mode": "additive", "keys": [[0, 0], [0.5, 5], [1.0, 0]]}}, **kw}


def cue(id, tier="free", actions=None, **kw):
    return {"id": id, "kind": "cue", "tier": tier, "description": f"{id} cue",
            "actions": actions if actions is not None else [{"at": 0, "do": "sfx", "id": "beep"}], **kw}


def seq(id, tier="show", track=None, **kw):
    return {"id": id, "kind": "sequence", "tier": tier, "description": f"{id} seq",
            "track": track if track is not None else [], **kw}


def errors(lib, item_id=None):
    return [i.message for i in lib.issues if i.severity == "error" and (item_id is None or i.where == item_id)]


# ---------------------------------------------------------------------------- expansion details

def test_expand_time_clock_nested_cue_offsets_are_seconds():
    lib = ShowLibrary.from_items([
        cue("c", actions=[{"at": 0.1, "do": "chest", "command": "X2"}]),
        seq("s", track=[{"at": 2, "cue": "c"}, {"at": 1, "do": "duck"}]),
    ])
    assert expand(lib, "s") == [
        {"t": 1.0, "do": "duck"},
        {"t": 2.1, "do": "chest", "command": "X2", "hold": 0.0},
    ]


def test_expand_beat_clock_uses_sequence_bpm_without_live_tempo():
    lib = ShowLibrary.from_items([clip("n"), seq("s", clock="beat", bpm=60, track=[{"at": 3, "clip": "n"}])])
    assert expand(lib, "s") == [{"t": 3.0, "do": "clip", "id": "n", "intensity": 1.0, "speed": 1.0}]
    assert expand(lib, "s", bpm=120)[0]["t"] == 1.5


def test_expand_single_clip_root():
    lib = ShowLibrary.from_items([clip("n")])
    assert expand(lib, "n") == [{"t": 0.0, "do": "clip", "id": "n", "intensity": 1.0, "speed": 1.0}]


# ---------------------------------------------------------------------------- models / validation

def test_structural_errors_leave_the_item_out():
    lib = ShowLibrary.from_items([
        {"id": "Bad-Id", "kind": "clip", "tier": "free", "duration": 1},
        clip("late_start", tracks={"visor": {"mode": "override", "keys": [[0.1, 5]]}}),
        clip("additive_drift", tracks={"visor": {"keys": [[0, 0], [1, 3]]}}),
        cue("no_pattern", actions=[{"do": "eyes"}]),
        seq("loop_no_len", loop=True),
        clip("ok"),
    ])
    assert set(lib.items) == {"ok"}
    assert len(errors(lib)) == 5


def test_tier_rule_forbids_including_a_higher_tier():
    lib = ShowLibrary.from_items([
        seq("big", tier="show"),
        cue("smuggler", tier="free", actions=[{"do": "clip", "id": "wave"}]),
        clip("wave", tier="cheap"),
        seq("sneaky", tier="cheap", track=[{"at": 0, "sequence": "big"}]),
    ])
    assert any("tier 'free' may not include clip 'wave'" in m for m in errors(lib, "smuggler"))
    assert any("tier 'cheap' may not include sequence 'big'" in m for m in errors(lib, "sneaky"))
    assert not lib.is_valid("smuggler") and lib.is_valid("wave")


def test_references_joints_params():
    lib = ShowLibrary.from_items([
        clip("claw", tracks={"hero_claw_left": {"keys": [[0, 0], [1, 0]]}}),
        clip("claw_ok", requires="extended", tracks={"hero_claw_left": {"keys": [[0, 0], [1, 0]]}}),
        clip("alien", tracks={"tail": {"keys": [[0, 0], [1, 0]]}}),
        seq("s", track=[{"at": 0, "cue": "nope"}, {"at": 0, "clip": "claw_ok", "speed": 3}]),
    ])
    assert any("needs \"requires\": \"extended\"" in m for m in errors(lib, "claw"))
    assert lib.is_valid("claw_ok")
    assert any("unknown joint" in m for m in errors(lib, "alien"))
    s_errors = errors(lib, "s")
    assert any("unknown cue 'nope'" in m for m in s_errors)
    assert any("speed 3" in m for m in s_errors)


def test_nesting_depth_and_cycles():
    chain = [seq(f"s{i}", track=[{"at": 0, "sequence": f"s{i+1}"}]) for i in range(4)] + [seq("s4")]
    lib = ShowLibrary.from_items(chain)
    # s0 -> s1 -> s2 -> s3 -> s4 is four nested levels below s0; three is the limit.
    assert any("nesting depth 4" in m for m in errors(lib, "s0"))
    assert lib.is_valid("s1")
    cyc = ShowLibrary.from_items([seq("a", track=[{"at": 0, "sequence": "b"}]),
                                  seq("b", track=[{"at": 0, "sequence": "a"}])])
    assert any("cycle" in m for m in errors(cyc, "a"))


# ---------------------------------------------------------------------------- loader

def test_loader_tolerates_missing_and_empty_folders(tmp_path):
    assert len(load_library(tmp_path / "does-not-exist")) == 0
    (tmp_path / "clips").mkdir()
    assert len(load_library(tmp_path)) == 0


def test_loader_reads_files_and_reports_bad_ones(tmp_path):
    for folder in ("clips", "cues", "sequences"):
        (tmp_path / folder).mkdir()
    (tmp_path / "clips" / "nod.json").write_text(json.dumps(clip("nod")))
    (tmp_path / "clips" / "misnamed.json").write_text(json.dumps(clip("other")))
    (tmp_path / "cues" / "broken.json").write_text("{not json")
    (tmp_path / "cues" / "sneaky.json").write_text(json.dumps(seq("sneaky")))
    (tmp_path / "idle.json").write_text(json.dumps({"after_s": 45, "choices": []}))
    lib = load_library(tmp_path)
    assert set(lib.items) == {"nod"}
    assert lib.idle == {"after_s": 45, "choices": []}
    msgs = " | ".join(str(i) for i in lib.issues)
    assert "file name must equal the id" in msgs
    assert "unreadable JSON" in msgs
    assert "in the cues/ folder" in msgs


def test_handle_reloads_when_files_change(tmp_path):
    handle = ShowLibraryHandle(tmp_path)
    assert len(handle.library) == 0
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "nod.json").write_text(json.dumps(clip("nod")))
    assert "nod" in handle.library.items


def test_show_dir_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("SHOW_DIR", raising=False)
    assert resolve_show_dir() == DEFAULT_SHOW_DIR
    assert (DEFAULT_SHOW_DIR / "SPEC.md").exists()
    monkeypatch.setenv("SHOW_DIR", str(tmp_path))
    assert resolve_show_dir() == tmp_path
    assert resolve_show_dir("/x/y") == Path("/x/y")


def test_the_real_show_folder_loads_without_crashing():
    """Content is authored in parallel; whatever is there must at least load."""
    lib = load_library(DEFAULT_SHOW_DIR)
    assert isinstance(lib, ShowLibrary)


# ---------------------------------------------------------------------------- catalogue

def test_catalog_is_sorted_stable_and_tier_filtered():
    items = [
        clip("zeta", tier="free"), clip("alpha", tier="cheap"),
        cue("hype", tier="cheap", actions=[{"do": "clip", "id": "alpha"}]),
        cue("big_cue", tier="show"),
        seq("dj_intro", tier="show"), seq("groove", tier="cheap"), seq("tiny", tier="free"),
    ]
    a = build_catalog(ShowLibrary.from_items(items))
    b = build_catalog(ShowLibrary.from_items(list(reversed(items))))
    assert a.prompt_block == b.prompt_block  # byte-stable whatever the load order
    assert a.taggable == {("clip", "alpha"), ("clip", "zeta"), ("cue", "hype")}
    assert a.tool_ids == ("dj_intro", "groove")
    block = a.prompt_block
    assert block.index("- alpha") < block.index("- zeta")
    assert "big_cue" not in block and "tiny" not in block
    assert "never more than two" in block


def test_catalog_empty_library():
    cat = build_catalog(ShowLibrary.from_items([]))
    assert cat.empty and cat.prompt_block == ""
