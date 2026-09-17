"""The set_eye_color / set_eye_animation intent must reach the eye service intact.

Found by a full-system run on 2026-09-17: "make your eyes red" was classified correctly and
dispatched in 209 ms, and then thrown away. Two independent defects:

1. IntentRouterService routed the intent through CLI_COMMAND as
   `eye pattern <pattern> <color>` - two args. The compound command "eye pattern" is
   registered with max_args=1 (main.py:349), so command_decorators.py:368 rejected it with
   "Command 'eye pattern' accepts at most 1 arguments, got 2".
2. The default pattern was "solid", which is not a member of EyePattern
   (eye_light_controller_service.py:48). Even with the arg count fixed, the eye service would
   have raised ValueError and logged "Invalid eye pattern: solid".

Both are pre-existing - the Jev fast router only added `set_eye_animation` as an alias, which
is why it inherited the same breakage.
"""

import asyncio
from typing import Any, Dict, List

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.eye_light_controller_service import EyePattern
from cantina_os.services.intent_router_service import IntentRouterService


class _Probe:
    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    async def handler(self, payload=None):
        self.payloads.append(payload)


@pytest.fixture
async def router_rig():
    bus = AsyncIOEventEmitter()
    eye = _Probe()
    cli = _Probe()
    bus.on(EventTopics.EYE_COMMAND.value, eye.handler)
    bus.on(EventTopics.CLI_COMMAND.value, cli.handler)

    router = IntentRouterService(bus, {})
    await router.start()
    await asyncio.sleep(0.05)

    yield bus, router, eye, cli

    try:
        await router.stop()
    except Exception:
        pass


@pytest.mark.parametrize("intent_name", ["set_eye_color", "set_eye_animation"])
async def test_eye_intent_emits_eye_command_with_the_colour(router_rig, intent_name):
    bus, router, eye, cli = router_rig

    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": intent_name,
            "parameters": {"color": "red"},
            "original_text": "make your eyes red",
            "conversation_id": "turn-1",
        },
    )
    for _ in range(60):
        if eye.payloads:
            break
        await asyncio.sleep(0.02)

    assert eye.payloads, (
        f"{intent_name} emitted no EYE_COMMAND. The old code went through CLI_COMMAND, where "
        "the two-arg `eye pattern <pattern> <color>` form was rejected by the arg check."
    )
    payload = eye.payloads[0]
    assert payload["color"] == "red", "the colour must survive - the CLI grammar dropped it"
    assert payload["pattern"], "a pattern is required"


@pytest.mark.parametrize("intent_name", ["set_eye_color", "set_eye_animation"])
async def test_default_pattern_is_a_real_EyePattern_member(router_rig, intent_name):
    """The old default "solid" is not in the enum; the eye service rejected it outright."""
    bus, router, eye, cli = router_rig

    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": intent_name,
            "parameters": {"color": "blue"},
            "original_text": "turn your eyes blue",
            "conversation_id": "turn-2",
        },
    )
    for _ in range(60):
        if eye.payloads:
            break
        await asyncio.sleep(0.02)

    assert eye.payloads
    pattern = eye.payloads[0]["pattern"]
    # Raises ValueError if the router ever picks a non-member again.
    assert EyePattern(pattern) is not None
    assert pattern != "solid"


async def test_explicit_pattern_is_respected(router_rig):
    bus, router, eye, cli = router_rig

    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": "set_eye_animation",
            "parameters": {"color": "green", "pattern": "happy"},
            "original_text": "do your happy eyes",
            "conversation_id": "turn-3",
        },
    )
    for _ in range(60):
        if eye.payloads:
            break
        await asyncio.sleep(0.02)

    assert eye.payloads
    assert eye.payloads[0]["pattern"] == "happy"
    assert EyePattern(eye.payloads[0]["pattern"]) is EyePattern.HAPPY


async def test_no_colour_is_declined_rather_than_guessed(router_rig):
    bus, router, eye, cli = router_rig

    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": "set_eye_color",
            "parameters": {},
            "original_text": "change your eyes",
            "conversation_id": "turn-4",
        },
    )
    await asyncio.sleep(0.4)
    assert not eye.payloads, "with no colour the router must not invent one"
