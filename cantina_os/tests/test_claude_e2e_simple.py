"""
Simple End-to-End Tests for ClaudeService with REAL Anthropic API

This tests the actual Claude API integration without mocking.
"""

import os
import sys
import pytest
from anthropic import Anthropic


class TestClaudeAPIIntegration:
    """Test that Claude API works with the API key"""

    @pytest.fixture
    def api_key(self):
        """Get API key from environment"""
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            pytest.skip("ANTHROPIC_API_KEY not set")
        return key

    @pytest.fixture
    def client(self, api_key):
        """Create real Anthropic client"""
        return Anthropic(api_key=api_key)

    def test_api_key_valid(self, client):
        """Test 1: API key authentication works"""
        try:
            response = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=50,
                messages=[
                    {"role": "user", "content": "Say 'Hello from Claude API' and nothing else"}
                ]
            )
            assert response.content[0].text, "Should get response from Claude API"
            assert "Hello" in response.content[0].text
            print(f"✓ API key valid. Response: {response.content[0].text}")
        except Exception as e:
            pytest.fail(f"API call failed: {str(e)}")

    def test_streaming_works(self, client):
        """Test 2: Streaming responses work"""
        chunks = []
        try:
            with client.messages.stream(
                model="claude-3-5-haiku-20241022",
                max_tokens=100,
                messages=[
                    {"role": "user", "content": "Count to 5"}
                ]
            ) as stream:
                for text in stream.text_stream:
                    chunks.append(text)

            full_response = "".join(chunks)
            assert len(full_response) > 0, "Should have gotten streamed response"
            assert "1" in full_response or "one" in full_response.lower()
            print(f"✓ Streaming works. Chunks: {len(chunks)}, Response: {full_response}")
        except Exception as e:
            pytest.fail(f"Streaming failed: {str(e)}")

    def test_conversation_context(self, client):
        """Test 3: Multi-turn conversation context works"""
        messages = []
        try:
            # First turn
            messages.append({"role": "user", "content": "My favorite color is blue"})
            response1 = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=50,
                messages=messages
            )
            messages.append({"role": "assistant", "content": response1.content[0].text})

            # Second turn - ask about the previous context
            messages.append({"role": "user", "content": "What color did I just say?"})
            response2 = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=50,
                messages=messages
            )

            response_text = response2.content[0].text.lower()
            assert "blue" in response_text, f"Should remember blue from context, got: {response_text}"
            print(f"✓ Conversation context works. Response: {response_text}")
        except Exception as e:
            pytest.fail(f"Conversation context failed: {str(e)}")

    def test_tool_use(self, client):
        """Test 4: Tool use works"""
        tools = [
            {
                "name": "get_weather",
                "description": "Get weather for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"}
                    },
                    "required": ["location"]
                }
            }
        ]

        try:
            response = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=100,
                tools=tools,
                messages=[
                    {"role": "user", "content": "What's the weather in San Francisco?"}
                ]
            )

            # Check if Claude tried to use the tool
            has_tool_use = any(block.type == "tool_use" for block in response.content)
            assert has_tool_use, "Claude should have attempted to use the tool"
            print(f"✓ Tool use works. Stop reason: {response.stop_reason}")
        except Exception as e:
            pytest.fail(f"Tool use failed: {str(e)}")

    def test_response_format(self, client):
        """Test 5: Response format matches expected structure"""
        try:
            response = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=50,
                messages=[
                    {"role": "user", "content": "Say hello"}
                ]
            )

            # Verify response structure
            assert hasattr(response, 'id'), "Response should have id"
            assert hasattr(response, 'content'), "Response should have content"
            assert hasattr(response, 'model'), "Response should have model"
            assert hasattr(response, 'stop_reason'), "Response should have stop_reason"
            assert len(response.content) > 0, "Response should have content blocks"
            assert response.content[0].type == "text", "First content block should be text"

            print(f"✓ Response format correct. ID: {response.id}, Stop: {response.stop_reason}")
        except Exception as e:
            pytest.fail(f"Response format check failed: {str(e)}")


class TestClaudeServiceWithAPI:
    """Test ClaudeService class against real API"""

    @pytest.fixture
    def api_key(self):
        """Get API key from environment"""
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            pytest.skip("ANTHROPIC_API_KEY not set")
        return key

    def test_claude_service_initialization(self, api_key):
        """Test that ClaudeService initializes with real API key"""
        try:
            from cantina_os.services.claude_service.claude_service import ClaudeService
            from pyee.asyncio import AsyncIOEventEmitter

            event_bus = AsyncIOEventEmitter()
            config = {
                "ANTHROPIC_API_KEY": api_key,
                "STREAMING": True,
            }

            service = ClaudeService(event_bus=event_bus, config=config)
            service._initialize()

            assert service.client is not None, "Client should be initialized"
            print(f"✓ ClaudeService initialization works")
        except Exception as e:
            pytest.fail(f"ClaudeService initialization failed: {str(e)}")

    def test_claude_service_api_call(self, api_key):
        """Test that ClaudeService can make real API calls"""
        try:
            from cantina_os.services.claude_service.claude_service import ClaudeService, SessionMemory
            from pyee.asyncio import AsyncIOEventEmitter

            event_bus = AsyncIOEventEmitter()
            config = {
                "ANTHROPIC_API_KEY": api_key,
                "STREAMING": False,  # Non-streaming for simpler test
            }

            service = ClaudeService(event_bus=event_bus, config=config)
            service._initialize()
            service.memory = SessionMemory()

            # Add a message
            service.memory.add_message("user", "Say hello")

            # Make an API call using the service's client
            response = service.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=50,
                system="You are a helpful assistant",
                messages=service.memory.get_messages_for_api()
            )

            assert response.content[0].text, "Should get response"
            print(f"✓ ClaudeService API call works. Response: {response.content[0].text}")
        except Exception as e:
            pytest.fail(f"ClaudeService API call failed: {str(e)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
