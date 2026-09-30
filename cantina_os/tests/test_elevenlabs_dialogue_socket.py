"""DialogueSocket and ElevenLabsService's socket-or-HTTP choice, against a fake server."""

import base64
import json

import pytest

from cantina_os.services.elevenlabs_dialogue_socket import DialogueSocket, supports_model, to_absolute
from cantina_os.services.elevenlabs_service import ElevenLabsService


class FakeWS:
    """Scripted server: records what the client sends, replays canned frames."""

    def __init__(self, frames):
        self.sent = []
        self.frames = list(frames)
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self, timeout=None):
        if not self.frames:
            raise TimeoutError("no more frames")
        f = self.frames.pop(0)
        if isinstance(f, Exception):
            raise f
        return json.dumps(f)

    def close(self):
        self.closed = True


def audio(pcm: bytes, chars=None):
    msg = {"audio": base64.b64encode(pcm).decode()}
    if chars:
        msg["alignment"] = {"chars": list(chars), "char_start_times_ms": [i * 10 for i in range(len(chars))],
                            "char_durations_ms": [10] * len(chars)}
    return msg


def make(frames_per_connection):
    conns = [FakeWS(f) for f in frames_per_connection]
    opened = []

    def connect(url, key):
        ws = conns[len(opened)]
        opened.append(url)
        return ws

    sock = DialogueSocket("key", "voice-r3x", "eleven_v4_turbo", stability=0.6, connect=connect, keepalive_s=999)
    return sock, conns, opened


def test_whole_reply_is_one_turn_with_alignment():
    sock, (ws,), opened = make([[audio(b"\x01\x00" * 4, "Hi"), audio(b"\x02\x00" * 4, "!"),
                                 {"is_final_audio_for_turn": True}]])
    out = list(sock.synthesize("Hi!"))

    assert "model_id=eleven_v4_turbo" in opened[0] and "sync_alignment=true" in opened[0]
    assert ws.sent[0] == {"voices": ["voice-r3x"], "voice_settings": {"stability": 0.6}}
    assert ws.sent[1] == {"inputs": [{"text": "Hi!", "voice_id": "voice-r3x", "new_turn": True}]}
    assert ws.sent[2] == {"flush": True}
    assert [len(pcm) for pcm, _ in out] == [8, 8]
    assert out[0][1]["chars"] == ["H", "i"]
    assert sock.connected  # stays warm for the next reply


def test_connection_is_reused_across_replies():
    frames = [audio(b"\x00\x00", "a"), {"is_final_audio_for_turn": True}] * 2
    sock, (ws,), opened = make([frames])
    list(sock.synthesize("a"))
    list(sock.synthesize("a"))
    assert len(opened) == 1
    assert sum(1 for m in ws.sent if "voices" in m) == 1


def test_server_error_drops_the_socket_and_the_next_reply_reconnects():
    sock, (bad, good), opened = make([
        [{"error": "boom", "code": "x"}],
        [audio(b"\x00\x00"), {"is_final_audio_for_turn": True}],
    ])
    with pytest.raises(RuntimeError):
        list(sock.synthesize("a"))
    assert bad.closed and not sock.connected
    assert len(list(sock.synthesize("a"))) == 1
    assert len(opened) == 2


def test_abandoning_a_turn_mid_stream_does_not_leak_audio_into_the_next():
    sock, (first, second), _ = make([
        [audio(b"\x00\x00"), audio(b"\x00\x00"), {"is_final_audio_for_turn": True}],
        [audio(b"\x00\x00"), {"is_final_audio_for_turn": True}],
    ])
    gen = sock.synthesize("long reply")
    next(gen)
    gen.close()  # consumer stopped (shutdown / error)
    assert first.closed and not sock.connected
    assert len(list(sock.synthesize("next"))) == 1


def test_only_handles_its_own_model_and_voice():
    sock, _, _ = make([[]])
    assert sock.handles("eleven_v4_turbo", "voice-r3x")
    assert not sock.handles("eleven_flash_v2_5", "voice-r3x")
    assert not sock.handles("eleven_v4_turbo", "someone-else")
    assert supports_model("eleven_v4_turbo") and not supports_model("eleven_v3")


def test_alignment_is_rebased_onto_the_reply_timeline():
    t = to_absolute({"chars": ["a", "b"], "char_start_times_ms": [0, 40], "char_durations_ms": [40, 30]}, 500.0)
    assert t == {"chars": ["a", "b"], "char_start_ms": [500.0, 540.0], "char_duration_ms": [40.0, 30.0]}


class _FakeHTTP:
    class text_to_speech:
        calls = []

        @classmethod
        def stream(cls, **kw):
            cls.calls.append(kw)
            return iter([b"\x05\x00\x05\x00", {"meta": True}])


def _service_with(sock):
    svc = ElevenLabsService.__new__(ElevenLabsService)  # skip __init__: only the helper is under test
    import logging, threading
    svc._dialogue_socket = sock
    svc._stop_event = threading.Event()
    svc._logger = logging.getLogger("test")
    return svc


def test_socket_failure_before_audio_falls_back_to_http():
    sock, _, _ = make([[TimeoutError("server silent")]])
    svc = _service_with(sock)
    _FakeHTTP.text_to_speech.calls.clear()
    out = list(svc._open_audio_stream(_FakeHTTP, "hi", "voice-r3x", "eleven_v4_turbo", {"stability": 0.6}))
    assert out == [(b"\x05\x00\x05\x00", None)]
    assert _FakeHTTP.text_to_speech.calls[0]["model_id"] == "eleven_v4_turbo"


def test_v4_lines_use_the_socket_and_others_use_http():
    sock, _, _ = make([[audio(b"\x01\x00", "h"), {"is_final_audio_for_turn": True}]])
    svc = _service_with(sock)
    _FakeHTTP.text_to_speech.calls.clear()
    out = list(svc._open_audio_stream(_FakeHTTP, "h", "voice-r3x", "eleven_v4_turbo", {}))
    assert out[0][1]["chars"] == ["h"] and not _FakeHTTP.text_to_speech.calls

    out = list(svc._open_audio_stream(_FakeHTTP, "h", "voice-r3x", "eleven_flash_v2_5", {}))
    assert out == [(b"\x05\x00\x05\x00", None)]


def test_keepalive_is_timed_from_the_last_message_we_sent_not_audio_received():
    # The server's 20 s idle timer only counts client messages; a long reply full of
    # received audio must not postpone the keep-alive (the 2026-09-29 1008 disconnect).
    import time as _t
    frames = [audio(b"\x00\x00")] * 3 + [{"is_final_audio_for_turn": True}]
    sock, (ws,), _ = make([frames])
    list(sock.synthesize("a"))
    sent_at = sock._last_send
    assert _t.monotonic() - sent_at < 1.0
    ws.frames = []
    sock._last_send -= 11  # pretend the flush went out 11 s ago
    sock._keepalive_s = 10
    sock.start()  # starts the keep-alive thread
    for _ in range(40):
        if any(m.get("keep_alive") for m in ws.sent):
            break
        _t.sleep(0.05)
    sock.close()
    assert any(m.get("keep_alive") for m in ws.sent)
