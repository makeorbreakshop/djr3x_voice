#!/usr/bin/env python3
"""
Standalone CLI Test for CantinaOS

This script bypasses CLI input issues by directly injecting commands
to test whether the system components are working properly.
"""

import asyncio
import logging
import sys
import os
from typing import Dict, Any, List

from pyee.asyncio import AsyncIOEventEmitter

# Set up path to import from cantina_os package
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import CliCommandPayload
from cantina_os.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from cantina_os.services.command_dispatcher_service import CommandDispatcherService
from cantina_os.services.mode_command_handler_service import ModeCommandHandlerService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("cli_test")

class DirectCommandTester:
    """Directly injects commands to test CantinaOS without using CLI input."""
    
    def __init__(self):
        self.event_bus = AsyncIOEventEmitter()
        self.services = {}
        self.responses = []
        
        # Set up response handler
        self.event_bus.on(EventTopics.CLI_RESPONSE, self._handle_response)
        
    def _handle_response(self, payload):
        """Handle CLI response events."""
        message = payload.get('message', '')
        is_error = payload.get('is_error', False)
        
        prefix = "ERROR: " if is_error else "RESPONSE: "
        self.responses.append(f"{prefix}{message}")
        print(f"{prefix}{message}")
        
    async def setup_services(self):
        """Set up core services for testing."""
        # Create mode manager
        mode_manager = YodaModeManagerService(self.event_bus, {}, logger)
        
        # Create command dispatcher
        command_dispatcher = CommandDispatcherService(self.event_bus, {}, logger)
        
        # Create mode command handler
        mode_command_handler = ModeCommandHandlerService(
            self.event_bus,
            mode_manager,
            {},
            logger
        )
        
        # Store services
        self.services["yoda_mode_manager"] = mode_manager
        self.services["command_dispatcher"] = command_dispatcher
        self.services["mode_command_handler"] = mode_command_handler
        
        # Start services
        for name, service in self.services.items():
            await service.start()
            logger.info(f"Started service: {name}")
            
        # Register commands
        await self._register_commands(command_dispatcher)
        logger.info("Registered commands")
            
    async def _register_commands(self, dispatcher):
        """Register commands with the dispatcher."""
        commands = [
            "engage", "ambient", "disengage", 
            "status", "reset", "help"
        ]
        
        for command in commands:
            await dispatcher.register_command(
                command,
                "mode_command_handler",
                EventTopics.MODE_COMMAND
            )
        
    async def shutdown(self):
        """Shut down all services."""
        for name, service in reversed(list(self.services.items())):
            await service.stop()
            logger.info(f"Stopped service: {name}")
            
    async def inject_command(self, command, args=None):
        """Inject a command directly into the system."""
        if args is None:
            args = []
            
        logger.info(f"Injecting command: {command} {' '.join(args)}")
        
        # Clear previous responses
        self.responses = []
        
        # Create command payload
        payload = CliCommandPayload(
            command=command,
            args=args,
            raw_input=f"{command} {' '.join(args)}".strip()
        ).model_dump()
        
        # Determine the appropriate topic
        if command in ["engage", "disengage", "ambient", "status", "help", "reset"]:
            topic = EventTopics.MODE_COMMAND
        else:
            topic = EventTopics.CLI_COMMAND
            
        # Emit the command event
        self.event_bus.emit(topic, payload)
        
        # Wait for response processing
        await asyncio.sleep(0.5)
        
        return self.responses
        
    async def run_test_sequence(self):
        """Run a sequence of test commands."""
        try:
            # Set up services
            await self.setup_services()
            
            print("\n=== TESTING DIRECT COMMAND INJECTION ===\n")
            
            # Test help command
            print("\n--- Testing 'help' command ---")
            await self.inject_command("help")
            
            # Test status command
            print("\n--- Testing 'status' command ---")
            await self.inject_command("status")
            
            # Test mode transitions
            print("\n--- Testing mode transitions ---")
            print("\nChanging to AMBIENT mode:")
            await self.inject_command("ambient")
            await asyncio.sleep(0.5)
            
            print("\nChecking status:")
            await self.inject_command("status")
            
            print("\nChanging to INTERACTIVE mode:")
            await self.inject_command("engage")
            await asyncio.sleep(0.5)
            
            print("\nChecking status:")
            await self.inject_command("status")
            
            print("\nReturning to IDLE mode:")
            await self.inject_command("disengage")
            await asyncio.sleep(0.5)
            
            print("\nFinal status check:")
            await self.inject_command("status")
            
            print("\n=== TEST SEQUENCE COMPLETE ===\n")
            
        finally:
            # Shut down services
            await self.shutdown()

async def main():
    """Run the CLI test."""
    tester = DirectCommandTester()
    await tester.run_test_sequence()
    
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nError during test: {e}") 