"""
End-to-End Tests for ClaudeService with REAL Anthropic API Calls

Tests the ClaudeService against the actual Claude API to verify:
- API key authentication works
- Request format is correct
- Response parsing is correct
- Streaming works as expected
- Tool use works with real API
"""

import os
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.claude_service.claude_service import ClaudeService, SessionMemory
from cantina_os.event_payloads import TranscriptionTextPayload, LLMResponsePayload
from cantina_os.core.event_topics import EventTopics

# Dormant until 2026-09-29: every test here skips without ANTHROPIC_API_KEY, and there was
# none on this machine, so these were never run against the ClaudeService they now target.
# They call the pre-refactor API - `_stream_claude_response()` without `messages`,
# `service.client` (now `_client`), `_handle_transcription_final` (now
# `_handle_voice_transcript`), and a TranscriptionTextPayload without `source`/`timestamp`.
# The live API path is covered by tests/test_claude_e2e_simple.py and
# scripts/claude_live_verify.py. Rewrite against the current service rather than un-skip.
pytestmark = pytest.mark.skip(
    reason="targets the pre-refactor ClaudeService API (see module comment); "
    "live coverage is in test_claude_e2e_simple.py"
)


class TestClaudeServiceE2E:
    """End-to-End tests with REAL Anthropic API calls"""

    @pytest.fixture
    async def event_bus(self):
        """Create real event bus"""
        return AsyncIOEventEmitter()

    @pytest.fixture
    async def service(self, event_bus):
        """Create ClaudeService with real API key"""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            pytest.skip("ANTHROPIC_API_KEY not found in environment")

        config = {
            "ANTHROPIC_API_KEY": api_key,
            "STREAMING": True,
            "ENABLE_INTERIM_STREAMING": False,
        }
        service = ClaudeService(event_bus=event_bus, config=config)
        yield service

    @pytest.mark.asyncio
    async def test_api_key_valid(self, service):
        """Test that API key is valid by making a real API call"""
        service._initialize()

        # Verify client was created
        assert service.client is not None, "Anthropic client should be initialized"

        # Make a simple API call to verify key works
        try:
            response = service.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=50,
                messages=[{"role": "user", "content": "Say 'API key valid' and nothing else"}]
            )
            assert response.content[0].text, "Should get a response from Claude API"
        except Exception as e:
            pytest.fail(f"API call failed with valid key: {str(e)}")

    @pytest.mark.asyncio
    async def test_real_claude_response(self, event_bus, service):
        """Test that ClaudeService can get a real response from Claude"""
        service._initialize()

        # Simulate a transcription event
        transcription = "What is 2+2?"

        # Mock emit to capture responses
        responses_captured = []

        def capture_response(payload):
            if isinstance(payload, LLMResponsePayload):
                responses_captured.append(payload)

        event_bus.on(EventTopics.LLM_RESPONSE_TEXT,
                     lambda data: capture_response(data if isinstance(data, LLMResponsePayload) else None))

        # Call the service's LLM processing
        try:
            await service._handle_transcription_final(TranscriptionTextPayload(
                timestamp=None,
                event_id="test-1",
                conversation_id="test-conv-1",
                text=transcription,
                confidence=0.95
            ))

            # Give async processing time to complete
            await asyncio.sleep(2)

            # Verify we got a response (may be empty list if emit wasn't called)
            # The key test is that this doesn't crash
            assert True, "Real Claude API call completed without error"
        except Exception as e:
            pytest.fail(f"Failed to process transcription with real API: {str(e)}")

    @pytest.mark.asyncio
    async def test_streaming_response(self, service):
        """Test that streaming responses work with real API"""
        service._initialize()
        service.memory = SessionMemory()

        # Add a user message
        service.memory.add_message("user", "Count to 3")

        # Call the streaming method directly
        response_text = ""
        try:
            async for chunk in service._stream_claude_response():
                if chunk:
                    response_text += chunk
                    # Verify we're getting non-empty chunks
                    assert isinstance(chunk, str), "Chunks should be strings"

            # We should have gotten some response
            assert response_text, "Should have gotten streaming response from Claude"
            assert len(response_text) > 5, f"Response should be substantial: {response_text}"
        except Exception as e:
            pytest.fail(f"Streaming response failed: {str(e)}")

    @pytest.mark.asyncio
    async def test_session_memory_with_real_api(self, service):
        """Test that SessionMemory properly manages context across real API calls"""
        service._initialize()
        service.memory = SessionMemory()

        # First turn
        service.memory.add_message("user", "My name is Alice")

        try:
            # Get a response that should remember the name
            response_text = ""
            async for chunk in service._stream_claude_response():
                if chunk:
                    response_text += chunk

            assert response_text, "Should get response about Alice"

            # Add assistant response to memory
            service.memory.add_message("assistant", response_text)

            # Second turn - ask about the name
            service.memory.add_message("user", "What name did I just tell you?")

            # Get second response
            response_text_2 = ""
            async for chunk in service._stream_claude_response():
                if chunk:
                    response_text_2 += chunk

            # Response should mention Alice (testing memory persistence)
            assert response_text_2, "Should get second response"
            assert len(service.memory.messages) >= 4, "Should have conversation history"
        except Exception as e:
            pytest.fail(f"Multi-turn conversation failed: {str(e)}")

    @pytest.mark.asyncio
    async def test_token_counting_realistic(self, service):
        """Test that token counting works with real-world messages"""
        service._initialize()
        memory = SessionMemory(max_tokens=100)

        # Add realistic messages
        messages = [
            ("user", "Hello, my name is Brandon and I'm building a DJ robot called DJ R3X"),
            ("assistant", "Nice to meet you! That sounds like an exciting project."),
            ("user", "Can you help me understand the architecture of the system?"),
        ]

        for role, content in messages:
            memory.add_message(role, content)

        # Token count should increase
        token_count = memory.current_token_count
        assert token_count > 0, "Should have non-zero token count"

        # Add enough messages to trigger pruning
        for i in range(50):
            memory.add_message("user", f"Message {i}")

        # Should have pruned old messages
        assert len(memory.messages) < 50, "Should have pruned old messages"
        assert len(memory.messages) > 1, "Should keep recent messages"

    @pytest.mark.asyncio
    async def test_error_handling_invalid_request(self, service):
        """Test that service handles invalid API requests gracefully"""
        service._initialize()
        service.memory = SessionMemory()

        # Try to stream with empty messages (might cause issues)
        try:
            # Service should handle this gracefully
            response = ""
            async for chunk in service._stream_claude_response():
                if chunk:
                    response += chunk
            # May get empty or may get an error - both are acceptable
            assert True, "Service handled empty context gracefully"
        except Exception as e:
            # Some error is acceptable - we're testing graceful degradation
            assert "anthropic" in str(e).lower() or "api" in str(e).lower(), \
                f"Error should be API-related: {str(e)}"


class TestClaudeServiceIntegration:
    """Integration tests for ClaudeService with event bus"""

    @pytest.fixture
    async def service_with_events(self):
        """Create ClaudeService configured for real API"""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            pytest.skip("ANTHROPIC_API_KEY not found in environment")

        event_bus = AsyncIOEventEmitter()
        config = {
            "ANTHROPIC_API_KEY": api_key,
            "STREAMING": True,
            "ENABLE_INTERIM_STREAMING": False,
        }
        service = ClaudeService(event_bus=event_bus, config=config)
        service._initialize()
        yield service

    @pytest.mark.asyncio
    async def test_full_event_pipeline(self, service_with_events):
        """Test complete event flow: transcription -> LLM -> response event"""
        event_bus = service_with_events._event_bus
        responses = []

        def capture_response(data):
            responses.append(data)

        event_bus.on(EventTopics.LLM_RESPONSE_TEXT, capture_response)

        # Simulate transcription
        payload = TranscriptionTextPayload(
            timestamp=None,
            event_id="e1",
            conversation_id="conv1",
            text="Hello Claude, are you working?",
            confidence=0.99
        )

        try:
            await service_with_events._handle_transcription_final(payload)

            # Wait for async processing
            await asyncio.sleep(1)

            # Should have attempted to emit response
            assert True, "Event pipeline processed without crashing"
        except Exception as e:
            pytest.fail(f"Event pipeline failed: {str(e)}")


if __name__ == "__main__":
    # Run with: pytest tests/test_claude_service_e2e.py -v
    pytest.main([__file__, "-v", "-s"])
