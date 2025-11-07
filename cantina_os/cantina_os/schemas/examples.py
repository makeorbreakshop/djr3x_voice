"""
DJ R3X CantinaOS - Schema System Examples and Testing

Examples and testing utilities for the socket.io command validation system.
Demonstrates proper usage patterns and provides validation testing framework.

This module serves as documentation and testing reference for the schema system.
"""

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import Mock

from .web_commands import (
    VoiceCommandSchema,
    MusicCommandSchema,
    DJCommandSchema,
    SystemCommandSchema
)
from .validation import (
    validate_socketio_command,
    SocketIOValidationMixin,
    COMMAND_SCHEMA_REGISTRY
)
from . import BaseWebResponse, WebCommandError


# Example valid command payloads
EXAMPLE_COMMANDS = {
    "voice_command": {
        "valid": [
            {"action": "start"},
            {"action": "stop"},
        ],
        "invalid": [
            {"action": "invalid_action"},
            {"action": ""},
            {},
        ]
    },
    
    "music_command": {
        "valid": [
            {"action": "play", "track_name": "Cantina Band"},
            {"action": "play", "track_id": "track_123"},
            {"action": "pause"},
            {"action": "resume"},
            {"action": "stop"},
            {"action": "next"},
            {"action": "queue", "track_name": "Imperial March"},
            {"action": "volume", "volume_level": 0.8},
        ],
        "invalid": [
            {"action": "volume", "volume_level": 1.5},  # Invalid volume range
            {"action": "invalid_action"},
            {"track_name": "Song"},  # Missing action
        ]
    },
    
    "dj_command": {
        "valid": [
            {"action": "start"},
            {"action": "start", "auto_transition": True, "transition_duration": 10.0},
            {"action": "stop"},
            {"action": "next"},
            {"action": "update_settings", "auto_transition": False, "genre_preference": "jazz"},
        ],
        "invalid": [
            {"action": "invalid_action"},
            {"action": "start", "transition_duration": 50.0},  # Too long
            {"action": "update_settings", "genre_preference": "x" * 60},  # Too long
        ]
    },
    
    "system_command": {
        "valid": [
            {"action": "set_mode", "mode": "INTERACTIVE"},
            {"action": "set_mode", "mode": "AMBIENT"},
            {"action": "restart"},
            {"action": "restart", "restart_delay": 10.0},
            {"action": "refresh_config"},
        ],
        "invalid": [
            {"action": "set_mode", "mode": "INVALID_MODE"},
            {"action": "restart", "restart_delay": 100.0},  # Too long
            {"action": "invalid_action"},
        ]
    }
}


class MockWebBridgeService(SocketIOValidationMixin):
    """Mock WebBridge service for testing validation."""
    
    def __init__(self):
        self._event_bus = Mock()
        self._sio = Mock()
        self._logger = Mock()
    
    async def emit_mock_event(self, topic: str, payload: Dict[str, Any]) -> None:
        """Mock event emission for testing."""
        self._event_bus.emit(topic, payload)


def test_command_validation() -> Dict[str, Any]:
    """
    Test all command schemas with valid and invalid examples.
    
    Returns:
        Dictionary with test results
    """
    results = {
        "total_tests": 0,
        "passed": 0,
        "failed": 0,
        "details": {}
    }
    
    for command_type, examples in EXAMPLE_COMMANDS.items():
        command_results = {
            "valid_tests": {"passed": 0, "failed": 0, "errors": []},
            "invalid_tests": {"passed": 0, "failed": 0, "errors": []}
        }
        
        # Test valid commands
        for valid_data in examples["valid"]:
            results["total_tests"] += 1
            try:
                schema = COMMAND_SCHEMA_REGISTRY.validate_command(command_type, valid_data)
                event_payload = schema.to_cantina_event()
                
                # Validation should succeed
                if isinstance(event_payload, dict) and "source" in event_payload:
                    command_results["valid_tests"]["passed"] += 1
                    results["passed"] += 1
                else:
                    command_results["valid_tests"]["failed"] += 1
                    command_results["valid_tests"]["errors"].append(
                        f"Invalid event payload for {valid_data}"
                    )
                    results["failed"] += 1
                    
            except Exception as e:
                command_results["valid_tests"]["failed"] += 1
                command_results["valid_tests"]["errors"].append(
                    f"Valid data failed: {valid_data} - {str(e)}"
                )
                results["failed"] += 1
        
        # Test invalid commands
        for invalid_data in examples["invalid"]:
            results["total_tests"] += 1
            try:
                schema = COMMAND_SCHEMA_REGISTRY.validate_command(command_type, invalid_data)
                
                # Validation should fail
                command_results["invalid_tests"]["failed"] += 1
                command_results["invalid_tests"]["errors"].append(
                    f"Invalid data incorrectly passed: {invalid_data}"
                )
                results["failed"] += 1
                
            except (WebCommandError, ValueError, Exception):
                # Expected to fail
                command_results["invalid_tests"]["passed"] += 1
                results["passed"] += 1
        
        results["details"][command_type] = command_results
    
    return results


def test_event_payload_generation() -> Dict[str, Any]:
    """
    Test event payload generation for all command types.
    
    Returns:
        Dictionary with payload generation test results
    """
    results = {
        "command_types": {},
        "total_commands": 0,
        "successful_payloads": 0
    }
    
    for command_type, examples in EXAMPLE_COMMANDS.items():
        command_payloads = []
        
        for valid_data in examples["valid"]:
            try:
                schema = COMMAND_SCHEMA_REGISTRY.validate_command(command_type, valid_data)
                payload = schema.to_cantina_event()
                
                command_payloads.append({
                    "input": valid_data,
                    "payload": payload,
                    "action": schema.action,
                    "command_id": schema.command_id
                })
                
                results["successful_payloads"] += 1
                
            except Exception as e:
                command_payloads.append({
                    "input": valid_data,
                    "error": str(e)
                })
            
            results["total_commands"] += 1
        
        results["command_types"][command_type] = command_payloads
    
    return results


async def test_socketio_validation_decorator():
    """Test the socket.io validation decorator."""
    mock_service = MockWebBridgeService()
    
    @validate_socketio_command("voice_command")
    async def mock_voice_handler(self, sid: str, validated_command: VoiceCommandSchema):
        """Mock voice command handler."""
        return validated_command
    
    # Test with valid data
    try:
        await mock_voice_handler(mock_service, "test_sid", {"action": "start"})
        print("✓ Decorator validation passed for valid data")
    except Exception as e:
        print(f"✗ Decorator validation failed for valid data: {e}")
    
    # Test with invalid data
    try:
        await mock_voice_handler(mock_service, "test_sid", {"action": "invalid"})
        print("✗ Decorator validation should have failed for invalid data")
    except Exception:
        print("✓ Decorator validation correctly rejected invalid data")


def print_schema_documentation():
    """Print comprehensive documentation for all schemas."""
    print("\n" + "=" * 60)
    print("DJ R3X Socket.IO Command Schema Documentation")
    print("=" * 60)
    
    for command_type in COMMAND_SCHEMA_REGISTRY.get_supported_commands():
        info = COMMAND_SCHEMA_REGISTRY.get_command_info(command_type)
        if info:
            print(f"\n{command_type.upper()}:")
            print(f"  Schema: {info['schema_class']}")
            print(f"  Actions: {', '.join(info['allowed_actions'])}")
            print(f"  Required Fields: {', '.join(info['required_fields'])}")
            if info['description']:
                print(f"  Description: {info['description']}")
            
            # Show examples
            examples = EXAMPLE_COMMANDS.get(command_type, {}).get("valid", [])
            if examples:
                print("  Examples:")
                for i, example in enumerate(examples[:3], 1):
                    print(f"    {i}. {json.dumps(example, indent=2)}")
    
    print("\n" + "=" * 60)


def run_comprehensive_tests():
    """Run all schema system tests and print results."""
    print("\n🧪 Running DJ R3X Schema System Tests")
    print("=" * 50)
    
    # Test 1: Command validation
    print("\n1. Testing Command Validation...")
    validation_results = test_command_validation()
    
    print(f"   Total Tests: {validation_results['total_tests']}")
    print(f"   Passed: {validation_results['passed']}")
    print(f"   Failed: {validation_results['failed']}")
    
    if validation_results['failed'] > 0:
        print("\n   Failed Test Details:")
        for cmd_type, details in validation_results['details'].items():
            if details['valid_tests']['errors'] or details['invalid_tests']['errors']:
                print(f"   {cmd_type}:")
                for error in details['valid_tests']['errors']:
                    print(f"     ✗ {error}")
                for error in details['invalid_tests']['errors']:
                    print(f"     ✗ {error}")
    
    # Test 2: Event payload generation
    print("\n2. Testing Event Payload Generation...")
    payload_results = test_event_payload_generation()
    
    print(f"   Total Commands: {payload_results['total_commands']}")
    print(f"   Successful Payloads: {payload_results['successful_payloads']}")
    
    # Test 3: Decorator functionality
    print("\n3. Testing Socket.IO Validation Decorator...")
    asyncio.run(test_socketio_validation_decorator())
    
    # Test 4: Schema registry
    print("\n4. Testing Schema Registry...")
    supported_commands = COMMAND_SCHEMA_REGISTRY.get_supported_commands()
    print(f"   Supported Commands: {len(supported_commands)}")
    print(f"   Commands: {', '.join(supported_commands)}")
    
    print("\n✅ All tests completed!")
    
    # Print documentation
    print_schema_documentation()


if __name__ == "__main__":
    run_comprehensive_tests()


# Export testing utilities
__all__ = [
    "EXAMPLE_COMMANDS",
    "MockWebBridgeService",
    "test_command_validation",
    "test_event_payload_generation",
    "test_socketio_validation_decorator",
    "print_schema_documentation",
    "run_comprehensive_tests"
]