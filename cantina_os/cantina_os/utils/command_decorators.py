"""
Command Decorators for CantinaOS

Simple but robust decorators for standardizing compound command registration
and handling across all services. This eliminates the need for complex
service-specific payload transformation logic.
"""

import logging
from typing import Dict, Any, Callable, Optional, List
from functools import wraps
from ..core.event_topics import EventTopics


class CompoundCommandRegistry:
    """Registry for compound commands with simple, consistent patterns."""
    
    def __init__(self):
        self.commands: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger(__name__)
    
    def register(self, command_pattern: str, service_name: str, handler_method: str, event_topic: str):
        """Register a compound command with its handler info."""
        self.commands[command_pattern.lower()] = {
            "service_name": service_name,
            "handler_method": handler_method,
            "event_topic": event_topic
        }
        self.logger.debug(f"Registered compound command: {command_pattern} -> {service_name}.{handler_method}")
    
    def get_handler_info(self, command_pattern: str) -> Optional[Dict[str, Any]]:
        """Get handler info for a command pattern."""
        return self.commands.get(command_pattern.lower())
    
    def list_commands(self) -> List[str]:
        """List all registered command patterns."""
        return list(self.commands.keys())


# Global registry instance
_command_registry = CompoundCommandRegistry()


def compound_command(command_pattern: str, event_topic: str = None):
    """
    Decorator for registering compound command handlers.
    
    Usage:
        @compound_command("eye test")
        async def handle_eye_test(self, payload: dict):
            # Handle the eye test command
            pass
    
    Args:
        command_pattern: The command pattern (e.g., "eye test", "dj start")
        event_topic: Optional event topic override (defaults to service's command topic)
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, payload: dict):
            # Standardize payload format before calling handler
            standardized_payload = standardize_command_payload(payload, command_pattern)
            return await func(self, standardized_payload)
        
        # Store command info on the function for registration
        wrapper._command_pattern = command_pattern
        wrapper._event_topic = event_topic
        wrapper._original_func = func
        
        return wrapper
    return decorator


def standardize_command_payload(payload: dict, command_pattern: str) -> dict:
    """
    Standardize command payload to consistent format.
    The decorator's command_pattern is the source of truth for what parts
    of the raw_input constitute the command itself vs. its arguments.
    
    Args:
        payload: Raw payload, typically from CommandDispatcher or another service.
                 Expected to contain at least "raw_input".
        command_pattern: Expected command pattern (e.g., "eye test", "list music").
        
    Returns:
        Standardized payload with consistent structure: 
        {command, subcommand, args, raw_input, conversation_id, ...any other original fields}.
    """
    pattern_parts = command_pattern.lower().split()
    base_command_from_pattern = pattern_parts[0] if pattern_parts else ""
    subcommand_from_pattern = pattern_parts[1] if len(pattern_parts) > 1 else None

    final_command = base_command_from_pattern
    final_subcommand = subcommand_from_pattern
    final_args = []

    raw_input_from_payload = payload.get("raw_input", "")
    raw_input_lower = raw_input_from_payload.lower()
    command_pattern_lower = command_pattern.lower()

    # Tokenize raw_input to compare with pattern_parts
    raw_input_parts = raw_input_from_payload.split()
    
    num_pattern_parts = len(pattern_parts)
    num_raw_input_parts = len(raw_input_parts)

    # Check if the raw_input starts with the command_pattern (token by token)
    is_pattern_match = False
    if num_raw_input_parts >= num_pattern_parts:
        is_pattern_match = True
        for i in range(num_pattern_parts):
            if raw_input_parts[i].lower() != pattern_parts[i]:
                is_pattern_match = False
                break
    
    if is_pattern_match:
        # The raw_input starts with the command_pattern.
        # The remaining parts of raw_input are the arguments.
        final_args = raw_input_parts[num_pattern_parts:]
    else:
        # Fallback: raw_input does not match the command_pattern token-wise.
        # This could be due to an alias or a differently structured payload from CommandDispatcher.
        # In this scenario, if CommandDispatcher has already parsed 'command', 'subcommand',
        # and 'args', we might use those 'args' IF they don't conflict with the pattern.
        # However, the primary goal is for the decorator's pattern to define args.
        # If the raw_input doesn't match the pattern, it's hard to reliably extract args
        # based *solely* on the pattern.
        # For now, if no direct pattern match on raw_input, we assume args might be as-is from payload,
        # or empty if not provided. This path should be less common if CLI always provides raw_input.
        
        # If CommandDispatcher provided command/subcommand that *match* the pattern,
        # then the payload['args'] are the true args.
        if payload.get("command", "").lower() == base_command_from_pattern and \
           (subcommand_from_pattern is None or payload.get("subcommand", "").lower() == subcommand_from_pattern):
            current_payload_args = payload.get("args", [])
            if isinstance(current_payload_args, list):
                final_args = current_payload_args
            elif isinstance(current_payload_args, str) and current_payload_args:
                final_args = current_payload_args.split()
        # else: final_args remains [] - indicating pattern didn't find args in raw_input

    # Build the standardized payload, starting with essentials
    standardized = {
        "command": final_command,
        "subcommand": final_subcommand, # This will be None if pattern has only one part
        "args": final_args,
        "raw_input": raw_input_from_payload,
        "conversation_id": payload.get("conversation_id") 
    }
    
    # Explicitly carry over timestamp and source if they exist in the original payload
    # This is important for compatibility with Pydantic models like DjCommandPayload
    # that expect these fields.
    if "timestamp" in payload:
        standardized["timestamp"] = payload["timestamp"]
    if "source" in payload:
        standardized["source"] = payload["source"]
            
    # Add all other fields from the original payload, without overwriting the core fields
    # or the explicitly carried-over timestamp/source.
    for key, value in payload.items():
        if key not in standardized:
            standardized[key] = value
            
    return ensure_standardcommandpayload_compatibility(standardized)


def validate_command_payload(payload: dict) -> bool:
    """
    Validate that a payload has the required command structure.
    
    Args:
        payload: Payload to validate
        
    Returns:
        True if payload has valid command structure, False otherwise
    """
    required_fields = ["command", "args", "raw_input"]
    
    # Check required fields exist
    for field in required_fields:
        if field not in payload:
            return False
    
    # Check field types
    if not isinstance(payload["command"], str):
        return False
    
    if not isinstance(payload["args"], list):
        return False
    
    if not isinstance(payload["raw_input"], str):
        return False
    
    # subcommand is optional but must be string if present
    if "subcommand" in payload and payload["subcommand"] is not None:
        if not isinstance(payload["subcommand"], str):
            return False
    
    return True


def ensure_standardcommandpayload_compatibility(payload: dict) -> dict:
    """
    Ensure payload structure exactly matches StandardCommandPayload fields.
    
    This validates that our standardized dict payloads are compatible with
    StandardCommandPayload structure for future type safety.
    
    Args:
        payload: Standardized payload dict
        
    Returns:
        Payload dict guaranteed to match StandardCommandPayload structure
    """
    # StandardCommandPayload fields: command, subcommand, args, raw_input, conversation_id
    compatible_payload = {
        "command": payload.get("command", ""),
        "subcommand": payload.get("subcommand"),  # Can be None
        "args": payload.get("args", []),
        "raw_input": payload.get("raw_input", ""),
        "conversation_id": payload.get("conversation_id")  # Can be None
    }
    
    # Explicitly carry over timestamp and source to the compatible_payload as well,
    # as these are part of base EventPayload and thus relevant for compatibility.
    if "timestamp" in payload:
        compatible_payload["timestamp"] = payload["timestamp"]
    if "source" in payload:
        compatible_payload["source"] = payload["source"]
    
    # Validate the structure
    if not validate_command_payload(compatible_payload):
        raise ValueError(f"Payload does not match StandardCommandPayload structure: {compatible_payload}")
    
    # Include any additional fields for backward compatibility
    for key, value in payload.items():
        if key not in compatible_payload:
            compatible_payload[key] = value
    
    return compatible_payload


def register_service_commands(service_instance, event_bus):
    """
    Auto-register all compound commands for a service instance.
    
    This scans the service for methods decorated with @compound_command
    and registers them with the CommandDispatcher.
    
    Args:
        service_instance: The service instance to scan
        event_bus: Event bus for emitting registration events
    """
    service_name = getattr(service_instance, 'service_name', service_instance.__class__.__name__)
    logger = logging.getLogger(__name__)
    
    # Scan for decorated methods
    for attr_name in dir(service_instance):
        attr = getattr(service_instance, attr_name)
        
        if hasattr(attr, '_command_pattern'):
            command_pattern = attr._command_pattern
            event_topic = attr._event_topic
            
            # Use service's default command topic if not specified
            if not event_topic:
                # Try to get service's default command topic
                if hasattr(service_instance, '_default_command_topic'):
                    event_topic = service_instance._default_command_topic
                else:
                    # Fallback to generic pattern
                    base_command = command_pattern.split()[0]
                    event_topic = f"{base_command.upper()}_COMMAND"
            
            # Register with global registry
            _command_registry.register(command_pattern, service_name, attr_name, event_topic)
            
            # Emit registration event to CommandDispatcher
            event_bus.emit(EventTopics.REGISTER_COMMAND, {
                "command": command_pattern,
                "handler_service": service_name,
                "event_topic": event_topic
            })
            
            logger.info(f"Auto-registered command: {command_pattern} -> {service_name}.{attr_name}")


def simple_command_handler(base_command: str):
    """
    Decorator for simple command handlers that automatically parse subcommands.
    
    Usage:
        @simple_command_handler("eye")
        async def handle_eye_command(self, payload: dict):
            subcommand = payload.get("subcommand")
            if subcommand == "test":
                await self.run_test()
            elif subcommand == "pattern":
                pattern = payload.get("args", [None])[0]
                await self.set_pattern(pattern)
    
    Args:
        base_command: The base command (e.g., "eye", "dj")
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, payload: dict):
            # Standardize payload for simple command handling
            standardized_payload = standardize_command_payload(payload, base_command)
            return await func(self, standardized_payload)
        
        wrapper._base_command = base_command
        return wrapper
    return decorator


# Import asyncio at the end to avoid circular imports
import asyncio


def validate_compound_command(min_args: int = 0, max_args: int = None, required_args: List[str] = None):
    """
    Decorator for adding validation to compound command handlers.
    
    Usage:
        @compound_command("eye pattern")
        @validate_compound_command(min_args=1, max_args=1, required_args=["pattern_name"])
        async def handle_eye_pattern(self, payload: dict):
            pattern = payload["args"][0]  # Safe to access after validation
            await self.set_pattern(pattern)
    
    Args:
        min_args: Minimum number of arguments required
        max_args: Maximum number of arguments allowed (None for unlimited)
        required_args: List of argument names for better error messages
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, payload: dict):
            # Validate payload structure first
            if not validate_command_payload(payload):
                error_msg = f"Invalid payload structure for command {func.__name__}"
                self.logger.error(error_msg)
                if hasattr(self, '_send_error'):
                    await self._send_error(error_msg)
                return
            
            args = payload.get("args", [])
            command = payload.get("command", "")
            subcommand = payload.get("subcommand", "")
            command_name = f"{command} {subcommand}" if subcommand else command
            
            # Validate argument count
            if len(args) < min_args:
                if required_args and len(required_args) >= min_args:
                    missing_args = required_args[len(args):min_args]
                    error_msg = f"Command '{command_name}' requires {min_args} arguments. Missing: {', '.join(missing_args)}"
                else:
                    error_msg = f"Command '{command_name}' requires at least {min_args} arguments, got {len(args)}"
                
                self.logger.error(error_msg)
                if hasattr(self, '_send_error'):
                    await self._send_error(error_msg)
                return
            
            if max_args is not None and len(args) > max_args:
                error_msg = f"Command '{command_name}' accepts at most {max_args} arguments, got {len(args)}"
                self.logger.error(error_msg)
                if hasattr(self, '_send_error'):
                    await self._send_error(error_msg)
                return
            
            # Call the original function with validated payload
            return await func(self, payload)
        
        return wrapper
    return decorator


def command_error_handler(func: Callable):
    """
    Decorator for standardized error handling in command methods.
    
    Usage:
        @compound_command("eye test")
        @command_error_handler
        async def handle_eye_test(self, payload: dict):
            # Command logic here
            pass
    """
    @wraps(func)
    async def wrapper(self, payload: dict):
        try:
            return await func(self, payload)
        except Exception as e:
            command = payload.get("command", "unknown")
            subcommand = payload.get("subcommand", "")
            command_name = f"{command} {subcommand}" if subcommand else command
            
            error_msg = f"Error executing command '{command_name}': {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            if hasattr(self, '_send_error'):
                await self._send_error(error_msg)
            
    return wrapper 