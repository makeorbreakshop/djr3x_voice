"""Claude's inline show tags: stream stripping, offsets, speech-relative scheduling, and the
ClaudeService integration (catalogue in the cached prompt, perform_show tool)."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.claude_service import ClaudeService
from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.show.tags import (
    AlignmentTimer,
    LinearCharTimer,
    SpeechTagScheduler,
    TagParser,
    extract_tags,
    timer_for_speech,
)

VALID = {("cue", "hype_drop"), ("clip", "nod"), ("clip", "wave")}
REPLY = "{clip:wave} Hey there, everyone! Time to {cue:hype_drop} get LOUD in here!"
CLEAN = "Hey there, everyone! Time to get LOUD in here!"


# ---------------------------------------------------------------------------- parsing

def test_extract_strips_and_records_offsets():
    clean, tags, dropped = extract_tags(REPLY, VALID)
    assert clean == CLEAN
    assert [(t.kind, t.id, t.offset) for t in tags] == [("clip", "wave", 0), ("cue", "hype_drop", CLEAN.index("get"))]
    assert dropped == []


@pytest.mark.parametrize("split", range(1, len(REPLY)))
def test_any_two_chunk_split_gives_the_same_clean_text_and_offsets(split):
    p = TagParser(valid=VALID)
    out = p.feed(REPLY[:split]) + p.feed(REPLY[split:]) + p.flush()
    assert out == CLEAN
    assert [t.offset for t in p.tags] == [0, CLEAN.index("get")]


def test_character_by_character_stream_never_leaks_a_brace():
    p = TagParser(valid=VALID)
    pieces = [p.feed(ch) for ch in REPLY] + [p.flush()]
    assert "".join(pieces) == CLEAN
    assert not any("{" in x or "}" in x for x in pieces)


def test_unknown_malformed_and_extra_tags_are_dropped_from_text():
    text = "{nod} One {cue:nope} two {clip:nod} three {clip:wave} four {cue:hype_drop} five"
    clean, tags, dropped = extract_tags(text, VALID)
    assert clean == "One two three four five"
    assert [t.id for t in tags] == ["nod", "wave"]  # at most two per reply
    assert dropped == ["{nod}", "{cue:nope}", "{cue:hype_drop}"]


def test_plain_braces_that_are_not_tags_survive_and_unclosed_tag_is_dropped():
    assert extract_tags("a {really long brace text that goes on and on and on past the limit ok} b", VALID)[0] \
        == "a {really long brace text that goes on and on and on past the limit ok} b"
    assert extract_tags("see you {cue:hy", VALID)[0] == "see you "
    assert extract_tags("x { y\nz", VALID)[0] == "x { y\nz"


def test_nothing_is_taggable_when_the_catalogue_is_empty():
    clean, tags, _ = extract_tags("Hi {clip:nod} there", frozenset())
    assert clean == "Hi there" and tags == []


# ---------------------------------------------------------------------------- timing

def test_timers():
    assert LinearCharTimer(15).seconds_at(30) == 2.0
    assert isinstance(timer_for_speech({}, 15), LinearCharTimer)
    aligned = timer_for_speech({"alignment": {"characters": list("abc"),
                                               "character_start_times_seconds": [0.0, 0.2, 0.5]}}, 15)
    assert isinstance(aligned, AlignmentTimer) and aligned.seconds_at(2) == 0.5 and aligned.seconds_at(99) == 0.5


async def test_scheduler_fires_after_speech_start_at_offset_over_chars_per_sec():
    fired = []
    t0 = time.monotonic()
    sched = SpeechTagScheduler(lambda i, c: fired.append((time.monotonic() - t0, i, c)), chars_per_sec=100)
    clean, tags, _ = extract_tags("  Hello {clip:nod} world, {cue:hype_drop} go!", VALID)
    sched.register("turn-1", clean, tags)
    assert sched.on_speech_started({"conversation_id": "someone-else"}) == []
    await asyncio.sleep(0.05)
    assert fired == []  # nothing before speech starts
    scheduled = sched.on_speech_started({"conversation_id": "turn-1", "text": clean.strip()})
    # offsets are into the spoken (stripped) text: "Hello world, go!"
    assert scheduled == [("nod", 0.06), ("hype_drop", 0.13)]
    await asyncio.sleep(0.25)
    assert [(i, c) for _, i, c in fired] == [("nod", "turn-1"), ("hype_drop", "turn-1")]
    assert abs(fired[0][0] - 0.05 - 0.06) < 0.03 and abs(fired[1][0] - 0.05 - 0.13) < 0.03
    assert sched.on_speech_started({"conversation_id": "turn-1"}) == []  # fires once


# ---------------------------------------------------------------------------- ClaudeService

def _show_dir(tmp_path):
    for folder in ("clips", "cues", "sequences"):
        (tmp_path / folder).mkdir()

    def write(folder, item):
        (tmp_path / folder / f"{item['id']}.json").write_text(json.dumps(item))

    keys = {"visor": {"mode": "override", "keys": [[0, 0], [0.3, 10]]}}
    write("clips", {"id": "wave", "kind": "clip", "tier": "free", "description": "A friendly wave", "duration": 0.5, "tracks": keys})
    write("clips", {"id": "nod", "kind": "clip", "tier": "free", "description": "Quick yes-nod", "duration": 0.5, "tracks": keys})
    write("cues", {"id": "hype_drop", "kind": "cue", "tier": "cheap", "description": "Arms up, airhorn",
                   "actions": [{"at": 0, "do": "sfx", "id": "airhorn"}]})
    write("sequences", {"id": "dj_intro", "kind": "sequence", "tier": "show", "description": "The big intro",
                        "track": [{"at": 0, "cue": "hype_drop"}]})
    return tmp_path


class FakeStream:
    def __init__(self, chunks):
        self.text_stream = iter(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return SimpleNamespace(content=[])


class FakeClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.requests = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        return FakeStream(self.chunks)


@pytest.fixture
def claude(tmp_path):
    bus = AsyncIOEventEmitter()
    svc = ClaudeService(bus, {"ANTHROPIC_API_KEY": "test", "SHOW_DIR": str(_show_dir(tmp_path)),
                              "SHOW_TAG_CHARS_PER_SEC": 100})
    svc._register_command_functions()
    svc._load_show_catalog()
    return bus, svc


def test_catalogue_goes_into_the_cached_system_prompt_and_tool_is_registered(claude):
    _, svc = claude
    prompt = svc._config["SYSTEM_PROMPT"]
    assert "<performance>" in prompt and "- hype_drop: Arms up, airhorn" in prompt
    assert "- nod: Quick yes-nod" in prompt and "- dj_intro: The big intro" in prompt
    tool = svc._tools["perform_show"]
    assert tool["input_schema"]["properties"]["id"]["enum"] == ["dj_intro"]
    before = svc._config["SYSTEM_PROMPT"]
    svc._load_show_catalog()  # idempotent: the cached prefix never grows or churns
    assert svc._config["SYSTEM_PROMPT"] == before


async def test_streamed_reply_is_clean_everywhere_and_tags_fire_after_speech_start(claude):
    bus, svc = claude
    chunks = ["{cl", "ip:wave} Hey there, every", "one! Time to {cue:hyp", "e_drop} get LOUD in here!"]
    svc._client = FakeClient(chunks)
    svc._current_conversation_id = "turn-7"
    llm, performs = [], []
    bus.on(EventTopics.LLM_RESPONSE.value, lambda p: llm.append(p))
    bus.on(EventTopics.SHOW_PERFORM.value, lambda p: performs.append((time.monotonic(), p)))

    await svc._stream_claude_response([{"role": "user", "content": "say hi and get hyped"}], suppress_tools=True)
    await asyncio.sleep(0.05)  # chunks are marshalled back onto the loop

    streamed = "".join(p["text"] for p in llm if not p["is_complete"])
    final = [p for p in llm if p["is_complete"]]
    assert streamed == CLEAN and final[-1]["text"] == CLEAN
    assert not any("{" in p["text"] for p in llm)
    # Claude's own memory keeps the tagged text, so it keeps seeing its convention.
    assert svc._memory.messages[-1].content == REPLY
    # tool_choice none (Jev already acted) and tags still work
    assert svc._client.requests[0]["tool_choice"] == {"type": "none"}
    assert performs == []

    t0 = time.monotonic()
    await svc._handle_speech_started_for_show_tags({"conversation_id": "turn-7", "text": CLEAN})
    await asyncio.sleep(0.35)
    assert [p["id"] for _, p in performs] == ["wave", "hype_drop"]
    assert all(p["source"] == "claude" and p["conversation_id"] == "turn-7" for _, p in performs)
    assert abs(performs[1][0] - t0 - CLEAN.index("get") / 100) < 0.03


async def test_non_taggable_items_in_a_reply_are_stripped_not_performed(claude):
    bus, svc = claude
    svc._current_conversation_id = "turn-8"
    llm = []
    bus.on(EventTopics.LLM_RESPONSE.value, lambda p: llm.append(p))
    await svc._emit_llm_response("{cue:dj_intro} Watch this {clip:nod} move!")
    assert llm[-1]["text"] == "Watch this move!"
    assert svc._show_tags.has_pending("turn-8")
    scheduled = svc._show_tags.on_speech_started({"conversation_id": "turn-8"})
    assert [i for i, _ in scheduled] == ["nod"]  # dj_intro is show-tier: tool only


async def test_perform_show_tool_call_becomes_show_perform_without_verbal_feedback(claude):
    bus, svc = claude
    router = IntentRouterService(bus)
    await router.start()
    performs, results = [], []
    bus.on(EventTopics.SHOW_PERFORM.value, lambda p: performs.append(p))
    bus.on(EventTopics.INTENT_EXECUTION_RESULT.value, lambda p: results.append(p))
    await asyncio.sleep(0.01)
    svc._current_conversation_id = "turn-9"
    await svc._process_tool_calls([{"type": "function", "id": "tool_1", "function": {
        "name": "perform_show", "arguments": json.dumps({"id": "dj_intro"})}}], "Here we go!")
    assert await _until(lambda: performs and results)
    assert performs[0] == {"id": "dj_intro", "params": None, "source": "claude", "conversation_id": "turn-9"}
    before = len(svc._memory.messages)
    spawned = []
    orig = asyncio.create_task
    asyncio.create_task = lambda coro, **kw: spawned.append(coro) or orig(coro, **kw)
    try:
        await svc._process_intent_execution_result(results[0])
    finally:
        asyncio.create_task = orig
    assert spawned == [] and len(svc._memory.messages) == before  # no second spoken reply
    await router.stop()


async def _until(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False
