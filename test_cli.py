#!/usr/bin/env python3
"""Test script for the rewritten CLIService with asyncio-based input handling."""

import asyncio
import logging
import sys
from pyee.asyncio import AsyncIOEventEmitter

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Import from the cantina_os package
from cantina_os.cantina_os.services.cli_service import CLIService
from cantina_os.cantina_os.event_topics import EventTopics

async def test_cli():
    """Run a test of the CLI service."""
    print("Starting CLI Service test")
    
    # Create event bus
    event_bus = AsyncIOEventEmitter()
    
    # Set up event handlers
    @event_bus.on(EventTopics.CLI_COMMAND)
    async def on_command(payload):
        print(f"\nReceived command: {payload['command']} with args: {payload['args']}")
        # Echo back a response
        event_bus.emit(EventTopics.CLI_RESPONSE, {
            "message": f"Command '{payload['command']}' processed",
            "success": True
        })
    
    @event_bus.on(EventTopics.CLI_RESPONSE)
    async def on_response(payload):
        print(f"Response handler: {payload}")
    
    @event_bus.on(EventTopics.MUSIC_COMMAND)
    async def on_music_command(payload):
        print(f"\nReceived music command: {payload['command']} with args: {payload['args']}")
        # Echo back a response
        event_bus.emit(EventTopics.CLI_RESPONSE, {
            "message": f"Music command '{payload['command']}' processed",
            "success": True
        })
    
    @event_bus.on(EventTopics.MODE_COMMAND)
    async def on_mode_command(payload):
        print(f"\nReceived mode command: {payload['command']} with args: {payload['args']}")
        # Echo back a response
        event_bus.emit(EventTopics.CLI_RESPONSE, {
            "message": f"Mode command '{payload['command']}' processed",
            "success": True
        })
    
    @event_bus.on(EventTopics.SYSTEM_SET_MODE_REQUEST)
    async def on_set_mode(payload):
        print(f"\nReceived set mode request: {payload['mode']}")
        # Echo back a response
        event_bus.emit(EventTopics.CLI_RESPONSE, {
            "message": f"Mode set to {payload['mode']}",
            "success": True
        })
    
    @event_bus.on(EventTopics.SYSTEM_SHUTDOWN)
    async def on_shutdown(payload):
        print("\nShutdown requested")
        # Echo back a response
        event_bus.emit(EventTopics.CLI_RESPONSE, {
            "message": "System shutting down...",
            "success": True
        })
        # Cancel the main task to exit
        asyncio.get_running_loop().call_later(2, task.cancel)
    
    # Create and start CLI service
    cli_service = CLIService(event_bus)
    await cli_service.start()
    
    print("\n--- CLI Service Test ---")
    print("Type commands to test. Try these examples:")
    print("  help             - Get help")
    print("  status           - Show status")
    print("  engage           - Set to INTERACTIVE mode")
    print("  ambient          - Set to AMBIENT mode")
    print("  disengage        - Set to IDLE mode")
    print("  play music test  - Test music command")
    print("  list music       - Test another music command")
    print("  quit             - Exit the test")
    print("---------------------------\n")
    
    try:
        # Create a task for the main loop
        task = asyncio.create_task(asyncio.sleep(600))  # Timeout after 10 minutes
        await task
    except asyncio.CancelledError:
        print("Test cancelled or timeout reached")
    finally:
        # Clean up
        await cli_service.stop()
        print("CLI Service stopped")

if __name__ == "__main__":
    try:
        asyncio.run(test_cli())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(0) 