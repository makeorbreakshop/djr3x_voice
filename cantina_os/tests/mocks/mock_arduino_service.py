"""
Mock Arduino Service for Testing

This module provides a mock implementation of the Arduino hardware interface
for testing the EyeLightControllerService without requiring actual hardware.
It simulates the single-character LED Eyes protocol with configurable timing and errors.
"""

import asyncio
import logging
import random
import time
from typing import Dict, List, Any, Optional, Callable, Awaitable, Set

from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.base_service import BaseService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatus

logger = logging.getLogger(__name__)


class MockSerialConnection:
    """
    Mock implementation of a Serial connection to Arduino.
    
    This simulates the behavior of the hardware connection with configurable
    response timing and error scenarios.
    """
    
    def __init__(
        self,
        response_delay_ms: int = 20,
        error_rate: float = 0.0,
        connection_drop_rate: float = 0.0,
        timeout_rate: float = 0.0
    ):
        """
        Initialize the mock serial connection.
        
        Args:
            response_delay_ms: Simulated response time in milliseconds
            error_rate: Probability (0-1) of sending an error response
            connection_drop_rate: Probability (0-1) of dropping the connection
            timeout_rate: Probability (0-1) of simulating a timeout
        """
        self.response_delay_ms = response_delay_ms
        self.error_rate = error_rate
        self.connection_drop_rate = connection_drop_rate
        self.timeout_rate = timeout_rate
        self.is_connected = True
        self.sent_data: List[str] = []
        self.received_data: List[str] = []
        self.supported_commands = set(['I', 'S', 'T', 'L', 'H', 'D', 'A', 'R']) | set('0123456789')
        
        # Mock state tracking
        self.current_mode = 'I'  # Default to Idle
        self.current_brightness = 5  # Default brightness (0-9)
        
    async def write(self, data: bytes) -> int:
        """
        Simulate writing data to the Arduino.
        
        Args:
            data: Bytes to write
            
        Returns:
            Number of bytes written
            
        Raises:
            ConnectionError: If the connection is dropped
            TimeoutError: If a timeout is simulated
        """
        if not self.is_connected:
            raise ConnectionError("Mock serial connection is closed")
            
        # Simulate connection drop
        if random.random() < self.connection_drop_rate:
            self.is_connected = False
            raise ConnectionError("Mock connection dropped")
            
        # Simulate timeout
        if random.random() < self.timeout_rate:
            raise TimeoutError("Mock serial write timeout")
            
        # Save the sent data
        data_str = data.decode('utf-8') if isinstance(data, bytes) else data
        self.sent_data.append(data_str)
        
        # Update internal state based on command
        cmd = data_str[0] if data_str else 'I'
        if cmd in self.supported_commands:
            if cmd in '0123456789':
                self.current_brightness = int(cmd)
            else:
                self.current_mode = cmd
        
        # Simulate processing delay
        await asyncio.sleep(self.response_delay_ms / 1000)
        
        return len(data)
        
    async def read(self, size: int = 1) -> bytes:
        """
        Simulate reading data from the Arduino.
        
        Args:
            size: Number of bytes to read
            
        Returns:
            Received bytes
            
        Raises:
            ConnectionError: If the connection is dropped
            TimeoutError: If a timeout is simulated
        """
        if not self.is_connected:
            raise ConnectionError("Mock serial connection is closed")
            
        # Simulate connection drop
        if random.random() < self.connection_drop_rate:
            self.is_connected = False
            raise ConnectionError("Mock connection dropped")
            
        # Simulate timeout
        if random.random() < self.timeout_rate:
            raise TimeoutError("Mock serial read timeout")
            
        # Generate a response based on the last sent command
        response = '-' if random.random() < self.error_rate else '+'
        self.received_data.append(response)
        
        # Simulate processing delay
        await asyncio.sleep(self.response_delay_ms / 1000)
        
        return response.encode('utf-8')
        
    async def close(self) -> None:
        """Close the mock serial connection."""
        self.is_connected = False
        
    def reset(self) -> None:
        """Reset the mock state."""
        self.is_connected = True
        self.sent_data = []
        self.received_data = []
        self.current_mode = 'I'
        self.current_brightness = 5
        
    @property
    def in_waiting(self) -> int:
        """Simulate bytes waiting to be read."""
        return 1 if self.is_connected else 0
    
    
class MockArduinoService(BaseService):
    """
    Mock service that simulates the Arduino hardware interface.
    
    This provides a complete mock implementation that responds to the same
    events as the actual EyeLightControllerService, but uses an in-memory
    simulation instead of hardware communication.
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the mock Arduino service.
        
        Args:
            event_bus: Event bus for inter-service communication
            config: Optional configuration dictionary
            logger: Optional logger instance
        """
        super().__init__("mock_arduino", event_bus, logger)
        
        # Configuration with defaults
        self._config = config or {}
        self.response_delay_ms = int(self._config.get("ARDUINO_RESPONSE_DELAY_MS", 20))
        self.error_rate = float(self._config.get("ARDUINO_ERROR_RATE", 0.0))
        self.connection_drop_rate = float(self._config.get("ARDUINO_CONNECTION_DROP_RATE", 0.0))
        self.timeout_rate = float(self._config.get("ARDUINO_TIMEOUT_RATE", 0.0))
        
        # Create the mock serial connection
        self._serial = MockSerialConnection(
            response_delay_ms=self.response_delay_ms,
            error_rate=self.error_rate,
            connection_drop_rate=self.connection_drop_rate,
            timeout_rate=self.timeout_rate
        )
        
        # State tracking
        self._animation_task = None
        self._commands_processed = 0
        self._last_command = None
        self._command_history: List[Dict[str, Any]] = []
        self._is_running = False
        
    async def _start(self) -> None:
        """Start the service."""
        # Set up event subscriptions
        self._subscribe_to_events({
            EventTopics.LED_COMMAND: self._handle_led_command,
            EventTopics.LED_ANIMATION_START: self._handle_animation_start,
            EventTopics.LED_ANIMATION_STOP: self._handle_animation_stop,
            EventTopics.MODE_CHANGED: self._handle_mode_change
        })
        
        self._is_running = True
        self.emit_status(ServiceStatus.RUNNING, "Mock Arduino service started")
        
    async def _stop(self) -> None:
        """Stop the service."""
        self._is_running = False
        
        # Cancel any running animation
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
            try:
                await self._animation_task
            except asyncio.CancelledError:
                pass
                
        # Close the mock serial connection
        await self._serial.close()
        
        # Unsubscribe from events
        self._unsubscribe_from_all_events()
        
        self.emit_status(ServiceStatus.STOPPED, "Mock Arduino service stopped")
        
    async def send_command(self, command: str) -> bool:
        """
        Send a command to the mock Arduino.
        
        Args:
            command: Single character command to send
            
        Returns:
            True if command was processed successfully, False otherwise
        """
        if not self._is_running:
            self.logger.warning("Cannot send command - service not running")
            return False
            
        try:
            # Record command history
            self._command_history.append({
                'command': command,
                'timestamp': time.time()
            })
            self._last_command = command
            self._commands_processed += 1
            
            # Send the command to the mock serial
            await self._serial.write(command)
            
            # Read the response
            response = await self._serial.read()
            
            # Emit success/failure events
            if response == b'+':
                self.emit(
                    EventTopics.LED_COMMAND_SUCCESS,
                    {'command': command}
                )
                return True
            else:
                self.emit(
                    EventTopics.LED_COMMAND_FAILURE,
                    {'command': command, 'error': 'Mock error response'}
                )
                return False
                
        except (ConnectionError, TimeoutError) as e:
            self.emit(
                EventTopics.LED_COMMAND_FAILURE,
                {'command': command, 'error': str(e)}
            )
            return False
            
    async def _handle_led_command(self, data: Dict[str, Any]) -> None:
        """
        Handle LED command events.
        
        Args:
            data: Event data with the command
        """
        command = data.get('command', '')
        if command:
            await self.send_command(command)
            
    async def _handle_animation_start(self, data: Dict[str, Any]) -> None:
        """
        Handle animation start events.
        
        Args:
            data: Event data with animation parameters
        """
        # Cancel any existing animation
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
            try:
                await self._animation_task
            except asyncio.CancelledError:
                pass
                
        # Start a new animation task
        animation_type = data.get('type', 'idle')
        self._animation_task = asyncio.create_task(
            self._run_animation(animation_type)
        )
        
    async def _handle_animation_stop(self, data: Dict[str, Any]) -> None:
        """
        Handle animation stop events.
        
        Args:
            data: Event data
        """
        # Cancel the animation task
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
            try:
                await self._animation_task
            except asyncio.CancelledError:
                pass
            
        # Return to idle mode
        await self.send_command('I')
        
    async def _handle_mode_change(self, data: Dict[str, Any]) -> None:
        """
        Handle mode change events.
        
        Args:
            data: Event data with the new mode
        """
        mode = data.get('mode', '')
        
        # Map system modes to LED commands
        mode_map = {
            'IDLE': 'I',
            'AMBIENT': 'H',  # Happy in ambient mode
            'INTERACTIVE': 'L',  # Listening in interactive mode
            'STARTUP': 'S'  # Speaking during startup
        }
        
        if mode in mode_map:
            await self.send_command(mode_map[mode])
            
    async def _run_animation(self, animation_type: str) -> None:
        """
        Run a simulated LED animation.
        
        Args:
            animation_type: Type of animation to run
        """
        try:
            if animation_type == 'idle':
                await self._run_idle_animation()
            elif animation_type == 'speaking':
                await self._run_speaking_animation()
            elif animation_type == 'listening':
                await self._run_listening_animation()
            elif animation_type == 'thinking':
                await self._run_thinking_animation()
            else:
                self.logger.warning(f"Unknown animation type: {animation_type}")
                
        except asyncio.CancelledError:
            # Animation was cancelled, clean up
            pass
            
    async def _run_idle_animation(self) -> None:
        """Run the idle animation."""
        while True:
            await self.send_command('I')
            await asyncio.sleep(1.0)
            
    async def _run_speaking_animation(self) -> None:
        """Run the speaking animation."""
        while True:
            await self.send_command('S')
            await asyncio.sleep(0.1)
            
    async def _run_listening_animation(self) -> None:
        """Run the listening animation."""
        while True:
            await self.send_command('L')
            await asyncio.sleep(0.2)
            
    async def _run_thinking_animation(self) -> None:
        """Run the thinking animation."""
        while True:
            await self.send_command('T')
            await asyncio.sleep(0.5)
            
    def reset_mock(self) -> None:
        """Reset the mock state."""
        self._serial.reset()
        self._commands_processed = 0
        self._last_command = None
        self._command_history = []
        
    def get_command_history(self) -> List[Dict[str, Any]]:
        """
        Get the command history.
        
        Returns:
            List of command records
        """
        return self._command_history.copy()
        
    def get_current_state(self) -> Dict[str, Any]:
        """
        Get the current state of the mock Arduino.
        
        Returns:
            Dictionary with current state
        """
        return {
            'mode': self._serial.current_mode,
            'brightness': self._serial.current_brightness,
            'is_connected': self._serial.is_connected,
            'commands_processed': self._commands_processed,
            'last_command': self._last_command
        }
        
    def set_error_rates(
        self,
        error_rate: Optional[float] = None,
        connection_drop_rate: Optional[float] = None,
        timeout_rate: Optional[float] = None
    ) -> None:
        """
        Set the error simulation rates.
        
        Args:
            error_rate: Probability of error responses
            connection_drop_rate: Probability of connection drops
            timeout_rate: Probability of timeouts
        """
        if error_rate is not None:
            self._serial.error_rate = error_rate
            
        if connection_drop_rate is not None:
            self._serial.connection_drop_rate = connection_drop_rate
            
        if timeout_rate is not None:
            self._serial.timeout_rate = timeout_rate
            
    def set_response_delay(self, delay_ms: int) -> None:
        """
        Set the response delay.
        
        Args:
            delay_ms: Response delay in milliseconds
        """
        self._serial.response_delay_ms = delay_ms 