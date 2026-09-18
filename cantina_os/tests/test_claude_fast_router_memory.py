from unittest.mock import MagicMock

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.claude_service import ClaudeService


@pytest.mark.asyncio
async def test_fast_router_result_is_not_duplicated_in_claude_memory():
    service = ClaudeService(
        AsyncIOEventEmitter(),
        {"OPENROUTER_API_KEY": "test-key", "CLAUDE_MODEL": "claude-sonnet-5"},
    )
    service._memory = MagicMock()

    await service._process_intent_execution_result(
        {
            "intent_name": "play_music",
            "parameters": {"track": "@semantic fun funky party"},
            "result": {"success": True, "track": "Bright Suns"},
            "success": True,
            "source": "jev_fast_router",
            "original_text": "play something fun",
            "conversation_id": "turn-1",
        }
    )

    service._memory.add_message.assert_not_called()
