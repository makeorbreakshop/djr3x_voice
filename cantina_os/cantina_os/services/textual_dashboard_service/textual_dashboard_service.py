"""
Textual Dashboard Service for CantinaOS

Provides a terminal-based TUI dashboard for real-time monitoring of DJ R3X system state.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional, Any, List
from collections import deque
from pydantic import BaseModel

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, Log, Label, Input
from textual.reactive import reactive

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    ServiceStatus,
    LogLevel,
    BaseEventPayload
)


class TextualDashboardService(BaseService):
    """
    Terminal UI dashboard service for real-time DJ R3X monitoring.

    Subscribes to key system events and displays them in an organized visual layout.
    Runs in the same process as other services with direct event bus access.

    EVENTS_IN: SERVICE_STATUS_UPDATE, TRANSCRIPTION_FINAL, MUSIC_PLAYBACK_STARTED,
               SYSTEM_MODE_CHANGED, DJ_MODE_CHANGED, LLM_RESPONSE_TEXT,
               SPEECH_SYNTHESIS_STARTED, TRACK_PLAYING
    EVENTS_OUT: None (dashboard is read-only)
    """

    class _Config(BaseModel):
        """Service configuration."""
        log_filter_level: str = "INFO"  # Minimum log level to display
        refresh_rate_hz: int = 10  # Update frequency
        max_log_lines: int = 100  # Event log buffer size
        enable_dashboard: bool = True  # Master switch for TUI (enabled by default with command input)

    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        name: str = "textual_dashboard"
    ):
        """Initialize the dashboard service.

        Args:
            event_bus: Event bus instance
            config: Optional configuration dictionary
            logger: Optional logger instance
            name: Service name for logging
        """
        super().__init__(name, event_bus, logger)

        # Parse configuration
        self._config = self._Config(**(config or {}))

        # Dashboard app instance
        self._dashboard_app: Optional[DJR3XDashboard] = None
        self._app_task: Optional[asyncio.Task] = None

        # State storage for dashboard updates
        self._service_states: Dict[str, Dict[str, Any]] = {}
        self._event_log: deque = deque(maxlen=self._config.max_log_lines)
        self._current_track: Optional[Dict[str, Any]] = None
        self._system_mode: str = "UNKNOWN"
        self._dj_mode_active: bool = False

        # Event loop reference for thread-safe updates
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    async def _start(self) -> None:
        """Initialize the service."""
        # Store event loop for thread-safe operations
        self._event_loop = asyncio.get_running_loop()

        # Always set up event subscriptions (track state even if TUI disabled)
        await self._setup_subscriptions()

        # Only start the TUI if enabled
        if not self._config.enable_dashboard:
            self.logger.info("Textual dashboard TUI disabled (event tracking active)")
            return

        # Create and start the Textual dashboard app
        self._dashboard_app = DJR3XDashboard(
            service=self,
            refresh_rate=self._config.refresh_rate_hz
        )

        # Run the dashboard in a background task
        self._app_task = asyncio.create_task(self._run_dashboard())

        self.logger.info("Textual dashboard service started successfully")

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions following guidelines."""
        # Use asyncio.gather to wait for all subscriptions
        await asyncio.gather(
            self.subscribe(EventTopics.SERVICE_STATUS_UPDATE, self._handle_service_status),
            self.subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription),
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started),
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_stopped),
            self.subscribe(EventTopics.TRACK_PLAYING, self._handle_track_playing),
            self.subscribe(EventTopics.SYSTEM_MODE_CHANGED, self._handle_mode_change),
            self.subscribe(EventTopics.LLM_RESPONSE_TEXT, self._handle_llm_response),
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech),
            self.subscribe(EventTopics.CLI_RESPONSE, self._handle_cli_response),
        )
        self.logger.debug("Event subscriptions set up successfully")

    async def _run_dashboard(self) -> None:
        """Run the Textual dashboard app."""
        try:
            await self._dashboard_app.run_async()
        except Exception as e:
            self.logger.error(f"Dashboard app error: {e}", exc_info=True)
            await self._emit_status(ServiceStatus.ERROR, f"Dashboard error: {e}")

    async def _stop(self) -> None:
        """Clean up resources."""
        # Stop the dashboard app
        if self._dashboard_app and self._dashboard_app.is_running:
            await self._dashboard_app.action_quit()

        # Cancel the app task
        if self._app_task and not self._app_task.done():
            self._app_task.cancel()
            try:
                await self._app_task
            except asyncio.CancelledError:
                pass

        self.logger.info("Textual dashboard service stopped")

    # ========================================================================
    # Event Handlers
    # ========================================================================

    async def _handle_service_status(self, payload: Dict[str, Any]) -> None:
        """Handle service status update events."""
        service_name = payload.get("service", "unknown")
        status = payload.get("status", "UNKNOWN")
        message = payload.get("message", "")

        # Update service state
        self._service_states[service_name] = {
            "status": status,
            "message": message,
            "timestamp": datetime.now()
        }

        # Add to event log
        self._add_log_entry("system", f"[{service_name}] {status}: {message}")

        # Update dashboard if running
        if self._dashboard_app:
            self._dashboard_app.update_service_status(service_name, status, message)

    async def _handle_transcription(self, payload: Dict[str, Any]) -> None:
        """Handle transcription final events."""
        text = payload.get("text", "")
        confidence = payload.get("confidence", 0.0)

        log_msg = f"[mic] Transcription: \"{text}\" (confidence: {confidence:.2f})"
        self._add_log_entry("transcription", log_msg)

        if self._dashboard_app:
            self._dashboard_app.add_event_log(log_msg)

    async def _handle_music_started(self, payload: Dict[str, Any]) -> None:
        """Handle music playback started events."""
        track_name = payload.get("track_name", "Unknown")

        log_msg = f"[music] Playback started: {track_name}"
        self._add_log_entry("music", log_msg)

        if self._dashboard_app:
            self._dashboard_app.add_event_log(log_msg)

    async def _handle_music_stopped(self, payload: Dict[str, Any]) -> None:
        """Handle music playback stopped events."""
        log_msg = "[music] Playback stopped"
        self._add_log_entry("music", log_msg)

        self._current_track = None

        if self._dashboard_app:
            self._dashboard_app.add_event_log(log_msg)
            self._dashboard_app.update_current_track(None)

    async def _handle_track_playing(self, payload: Dict[str, Any]) -> None:
        """Handle track playing events."""
        track_info = {
            "title": payload.get("track_name", "Unknown"),
            "artist": payload.get("artist", "Unknown Artist"),
            "duration": payload.get("duration", 0)
        }

        self._current_track = track_info

        if self._dashboard_app:
            self._dashboard_app.update_current_track(track_info)

    async def _handle_mode_change(self, payload: Dict[str, Any]) -> None:
        """Handle system mode change events."""
        new_mode = payload.get("mode", "UNKNOWN")
        old_mode = self._system_mode

        self._system_mode = new_mode

        log_msg = f"[system] Mode transition: {old_mode} → {new_mode}"
        self._add_log_entry("mode", log_msg)

        if self._dashboard_app:
            self._dashboard_app.update_system_mode(new_mode)
            self._dashboard_app.add_event_log(log_msg)

    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """Handle LLM response events."""
        response_text = payload.get("response_text", "")
        truncated = response_text[:60] + "..." if len(response_text) > 60 else response_text

        log_msg = f"[claude] Response: \"{truncated}\""
        self._add_log_entry("llm", log_msg)

        if self._dashboard_app:
            self._dashboard_app.add_event_log(log_msg)

    async def _handle_speech(self, payload: Dict[str, Any]) -> None:
        """Handle speech synthesis started events."""
        duration = payload.get("estimated_duration_seconds", 0.0)

        log_msg = f"[tts] Speech synthesis started ({duration:.1f}s duration)"
        self._add_log_entry("speech", log_msg)

        if self._dashboard_app:
            self._dashboard_app.add_event_log(log_msg)

    async def _handle_cli_response(self, payload: Dict[str, Any]) -> None:
        """Handle CLI response events."""
        message = payload.get("message", "")
        is_error = payload.get("is_error", False)

        # Add to event log with appropriate formatting
        if is_error:
            log_msg = f"[error] {message}"
        else:
            log_msg = f"[response] {message}"

        self._add_log_entry("cli", log_msg)

        if self._dashboard_app:
            self._dashboard_app.show_command_response(message, is_error)

    def _add_log_entry(self, category: str, message: str) -> None:
        """Add entry to internal event log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"{timestamp} {message}"
        self._event_log.append(entry)

    # ========================================================================
    # Public Interface for Dashboard App
    # ========================================================================

    def get_service_states(self) -> Dict[str, Dict[str, Any]]:
        """Get current service states."""
        return self._service_states.copy()

    def get_event_log(self) -> List[str]:
        """Get recent event log entries."""
        return list(self._event_log)

    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Get current playing track info."""
        return self._current_track.copy() if self._current_track else None

    def get_system_mode(self) -> str:
        """Get current system mode."""
        return self._system_mode

    def _emit_command(self, command: str) -> None:
        """Emit a CLI_COMMAND event for the given command.

        Args:
            command: The command string to execute
        """
        try:
            # Emit CLI_COMMAND event just like CLIService does
            self._event_bus.emit(EventTopics.CLI_COMMAND, {
                "command": command,
                "timestamp": datetime.now().isoformat()
            })
            self.logger.info(f"Command emitted: {command}")
        except Exception as e:
            self.logger.error(f"Error emitting command: {e}")


class DJR3XDashboard(App):
    """Textual TUI application for DJ-R3X monitoring."""

    CSS = """
    Screen {
        background: $surface;
    }

    #system_header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #services_container {
        height: 12;
        border: solid $primary;
        margin: 1;
    }

    #event_log_container {
        height: 1fr;
        border: solid $accent;
        margin: 1;
    }

    #music_container {
        height: 5;
        border: solid $success;
        margin: 1;
    }

    #command_container {
        height: 3;
        border: solid $primary;
        margin: 1;
    }

    #command_input {
        width: 100%;
    }

    #command_output {
        height: 1;
        color: $success;
    }

    DataTable {
        height: 100%;
    }

    Log {
        height: 100%;
    }
    """

    TITLE = "DJ R3X System Dashboard"

    # Reactive properties
    system_mode = reactive("INITIALIZING")
    dj_mode_active = reactive(False)

    def __init__(self, service: TextualDashboardService, refresh_rate: int = 10):
        """Initialize the dashboard app.

        Args:
            service: The TextualDashboardService instance
            refresh_rate: Update frequency in Hz
        """
        super().__init__()
        self._service = service
        self._refresh_interval = 1.0 / refresh_rate
        self._update_task: Optional[asyncio.Task] = None
        self._command_history: List[str] = []
        self._history_index: int = -1

    def compose(self) -> ComposeResult:
        """Create child widgets for the dashboard."""
        yield Header()

        # System status header
        yield Static(id="system_header")

        # Services status table
        with Container(id="services_container"):
            yield Label("Services Status:")
            yield DataTable(id="services_table")

        # Event log
        with Container(id="event_log_container"):
            yield Label("Event Log:")
            yield Log(id="event_log", auto_scroll=True)

        # Music player
        with Container(id="music_container"):
            yield Label("Current Track:")
            yield Static(id="music_info", renderable="No track playing")

        # Command input
        with Container(id="command_container"):
            yield Label("Command:")
            yield Input(id="command_input", placeholder="Type a command (e.g., 'play music 1', 'engage', 'quit')...")
            yield Static(id="command_output", renderable="")

        yield Footer()

    def on_mount(self) -> None:
        """Called when the dashboard is mounted."""
        # Set up services table
        table = self.query_one("#services_table", DataTable)
        table.add_columns("Service", "Status", "Message")
        table.cursor_type = "row"

        # Focus the command input
        input_widget = self.query_one("#command_input", Input)
        input_widget.focus()

        # Initial state update
        self._update_all_widgets()

        # Start periodic update task
        self._update_task = asyncio.create_task(self._periodic_update())

    async def on_unmount(self) -> None:
        """Called when the dashboard is unmounted."""
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle command input submission."""
        command = event.value.strip()

        if not command:
            return

        # Add to history
        self._command_history.append(command)
        self._history_index = len(self._command_history)

        # Clear input
        input_widget = self.query_one("#command_input", Input)
        input_widget.value = ""

        # Show command in output
        output_widget = self.query_one("#command_output", Static)
        output_widget.update(f"> {command}")

        # Log command to event log
        log_widget = self.query_one("#event_log", Log)
        log_widget.write(f"[bold cyan]> {command}[/bold cyan]")

        # Emit CLI_COMMAND event to the service
        self._service._emit_command(command)

    async def _periodic_update(self) -> None:
        """Periodically update dashboard with latest state."""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                self._update_all_widgets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._service.logger.error(f"Error in periodic update: {e}")

    def _update_all_widgets(self) -> None:
        """Update all dashboard widgets with current state."""
        # Update system header
        self._update_system_header()

        # Update services table
        self._update_services_table()

        # Update event log
        self._update_event_log()

        # Update music info
        self._update_music_info()

    def _update_system_header(self) -> None:
        """Update the system status header."""
        mode = self._service.get_system_mode()
        header_text = f"System Mode: {mode}"

        try:
            header = self.query_one("#system_header", Static)
            header.update(header_text)
        except Exception:
            pass

    def _update_services_table(self) -> None:
        """Update the services status table."""
        try:
            table = self.query_one("#services_table", DataTable)

            # Clear existing rows
            table.clear()

            # Add rows for each service
            services = self._service.get_service_states()
            for service_name, state in sorted(services.items()):
                status = state.get("status", "UNKNOWN")
                message = state.get("message", "")

                # Truncate message if too long
                if len(message) > 50:
                    message = message[:47] + "..."

                # Add status indicator
                if status == "RUNNING":
                    status_icon = "✓"
                elif status == "ERROR":
                    status_icon = "✗"
                else:
                    status_icon = "○"

                table.add_row(
                    service_name,
                    f"{status_icon} {status}",
                    message
                )
        except Exception as e:
            self._service.logger.debug(f"Error updating services table: {e}")

    def _update_event_log(self) -> None:
        """Update the event log widget."""
        try:
            log_widget = self.query_one("#event_log", Log)

            # Get new log entries (simple approach: write all entries)
            # In production, you'd track which entries are new
            entries = self._service.get_event_log()

            # Write recent entries (last 20)
            for entry in entries[-20:]:
                log_widget.write(entry)
        except Exception as e:
            self._service.logger.debug(f"Error updating event log: {e}")

    def _update_music_info(self) -> None:
        """Update the music player info."""
        try:
            music_widget = self.query_one("#music_info", Static)

            track = self._service.get_current_track()
            if track:
                info = f"♫ {track['title']} - {track['artist']}"
            else:
                info = "No track playing"

            music_widget.update(info)
        except Exception as e:
            self._service.logger.debug(f"Error updating music info: {e}")

    # ========================================================================
    # Public update methods called from service event handlers
    # ========================================================================

    def update_service_status(self, service_name: str, status: str, message: str) -> None:
        """Update a service's status in the table."""
        # Table will be updated on next periodic refresh
        pass

    def add_event_log(self, message: str) -> None:
        """Add an entry to the event log."""
        # Log will be updated on next periodic refresh
        pass

    def update_current_track(self, track_info: Optional[Dict[str, Any]]) -> None:
        """Update the current track display."""
        # Music info will be updated on next periodic refresh
        pass

    def update_system_mode(self, mode: str) -> None:
        """Update the system mode display."""
        self.system_mode = mode

    def show_command_response(self, message: str, is_error: bool = False) -> None:
        """Show command response in the output area.

        Args:
            message: Response message to display
            is_error: Whether this is an error message
        """
        try:
            output_widget = self.query_one("#command_output", Static)

            # Format message with color
            if is_error:
                formatted = f"[bold red]✗ {message}[/bold red]"
            else:
                formatted = f"[green]✓ {message}[/green]"

            output_widget.update(formatted)

            # Also add to event log
            log_widget = self.query_one("#event_log", Log)
            log_widget.write(formatted)

        except Exception as e:
            self._service.logger.debug(f"Error showing command response: {e}")
