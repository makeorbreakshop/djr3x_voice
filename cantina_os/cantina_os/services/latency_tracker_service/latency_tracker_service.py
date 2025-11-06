"""
LatencyTrackerService - Tracks and reports pipeline latency metrics.

This service listens to pipeline events and calculates latency metrics for:
- Transcription (voice start → transcription complete)
- LLM processing (transcription → LLM response)
- TTS generation (LLM response → TTS started)
- TTS playback duration (TTS started → TTS ended)
- Total pipeline latency (voice start → TTS ended)

Metrics are tracked per conversation_id and can be queried for analysis.
"""

import time
import asyncio
from typing import Dict, List, Optional, Any
from collections import OrderedDict

from ...base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import PerformanceMetricPayload


class LatencyTrackerService(BaseService):
    """
    Tracks latency metrics across the voice interaction pipeline.

    Collects timestamps from pipeline events and calculates stage durations:
    - Transcription latency
    - LLM processing latency
    - TTS generation latency
    - TTS playback duration
    - Total end-to-end latency
    """

    def __init__(self, event_bus, config=None):
        """
        Initialize the LatencyTrackerService.

        Args:
            event_bus: Event bus for communication
            config: Optional configuration dict with:
                - max_conversations: Max conversations to track (default: 100)
                - enable_auto_emit: Auto-emit DEBUG_PERFORMANCE events (default: True)
        """
        super().__init__(service_name="latency_tracker", event_bus=event_bus)

        # Store config
        self._config = config or {}

        # Configuration
        self._max_conversations = self._config.get("max_conversations", 100)
        self._enable_auto_emit = self._config.get("enable_auto_emit", True)

        # Active conversation metrics (keyed by conversation_id)
        self._conversation_metrics: Dict[str, Dict[str, float]] = OrderedDict()

        # Completed conversation metrics (for aggregate reporting)
        self._completed_metrics: List[Dict[str, Any]] = []

        self.logger.info(
            f"Initialized LatencyTrackerService "
            f"(max_conversations={self._max_conversations}, "
            f"auto_emit={self._enable_auto_emit})"
        )

    async def _setup_subscriptions(self):
        """Subscribe to pipeline events for latency tracking."""
        await asyncio.gather(
            self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_started),
            self.subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription_complete),
            self.subscribe(EventTopics.LLM_RESPONSE_TEXT, self._handle_llm_response),
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_tts_started),
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_tts_ended),
            self.subscribe(EventTopics.LATENCY_COMMAND, self.handle_latency_command),
        )

    async def _start(self):
        """Start the latency tracker service."""
        await self._setup_subscriptions()
        self.logger.info("LatencyTrackerService started")

    async def _stop(self):
        """Stop the latency tracker service."""
        self.logger.info(
            f"LatencyTrackerService stopping. "
            f"Tracked {len(self._conversation_metrics)} active conversations, "
            f"{len(self._completed_metrics)} completed."
        )

    # Event Handlers

    async def _handle_voice_started(self, payload: Dict[str, Any]):
        """Handle voice listening started event."""
        conversation_id = payload.get("conversation_id")
        timestamp = payload.get("timestamp", time.time())

        if not conversation_id:
            return

        # Initialize metrics for this conversation
        self._conversation_metrics[conversation_id] = {
            "voice_started": timestamp,
            "transcription_complete": None,
            "llm_complete": None,
            "tts_started": None,
            "tts_ended": None,
        }

        # Cleanup old conversations if needed
        self._cleanup_old_conversations()

    async def _handle_transcription_complete(self, payload: Dict[str, Any]):
        """Handle transcription complete event."""
        conversation_id = payload.get("conversation_id")
        timestamp = payload.get("timestamp", time.time())

        if not conversation_id or conversation_id not in self._conversation_metrics:
            return

        self._conversation_metrics[conversation_id]["transcription_complete"] = timestamp

    async def _handle_llm_response(self, payload: Dict[str, Any]):
        """Handle LLM response event."""
        conversation_id = payload.get("conversation_id")
        timestamp = payload.get("timestamp", time.time())

        if not conversation_id or conversation_id not in self._conversation_metrics:
            return

        self._conversation_metrics[conversation_id]["llm_complete"] = timestamp

    async def _handle_tts_started(self, payload: Dict[str, Any]):
        """Handle TTS started event."""
        conversation_id = payload.get("conversation_id")
        timestamp = payload.get("timestamp", time.time())

        if not conversation_id or conversation_id not in self._conversation_metrics:
            return

        self._conversation_metrics[conversation_id]["tts_started"] = timestamp

    async def _handle_tts_ended(self, payload: Dict[str, Any]):
        """Handle TTS ended event - marks conversation complete."""
        conversation_id = payload.get("conversation_id")
        timestamp = payload.get("timestamp", time.time())

        if not conversation_id or conversation_id not in self._conversation_metrics:
            return

        self._conversation_metrics[conversation_id]["tts_ended"] = timestamp

        # Calculate and emit metrics now that conversation is complete
        summary = self.get_conversation_summary(conversation_id)
        if summary and self._enable_auto_emit:
            await self._emit_performance_metrics(conversation_id, summary)

        # Move to completed metrics
        if summary:
            self._completed_metrics.append(summary)

    # Public API Methods

    def get_conversation_summary(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        Get latency summary for a specific conversation.

        Args:
            conversation_id: The conversation to summarize

        Returns:
            Dict with calculated latencies, or None if conversation not found
        """
        if conversation_id not in self._conversation_metrics:
            return None

        metrics = self._conversation_metrics[conversation_id]

        # Calculate stage latencies
        transcription_latency = None
        if metrics["voice_started"] and metrics["transcription_complete"]:
            transcription_latency = metrics["transcription_complete"] - metrics["voice_started"]

        llm_latency = None
        if metrics["transcription_complete"] and metrics["llm_complete"]:
            llm_latency = metrics["llm_complete"] - metrics["transcription_complete"]

        tts_generation_latency = None
        if metrics["llm_complete"] and metrics["tts_started"]:
            tts_generation_latency = metrics["tts_started"] - metrics["llm_complete"]

        tts_playback_duration = None
        if metrics["tts_started"] and metrics["tts_ended"]:
            tts_playback_duration = metrics["tts_ended"] - metrics["tts_started"]

        total_latency = None
        if metrics["voice_started"] and metrics["tts_ended"]:
            total_latency = metrics["tts_ended"] - metrics["voice_started"]

        return {
            "conversation_id": conversation_id,
            "transcription_latency": transcription_latency,
            "llm_latency": llm_latency,
            "tts_generation_latency": tts_generation_latency,
            "tts_playback_duration": tts_playback_duration,
            "total_latency": total_latency,
            "timestamps": metrics.copy(),
        }

    def get_latency_report(self) -> Dict[str, Any]:
        """
        Get aggregate latency report across all conversations.

        Returns:
            Dict with aggregate statistics
        """
        if not self._completed_metrics:
            return {
                "total_conversations": 0,
                "avg_transcription_latency": None,
                "avg_llm_latency": None,
                "avg_tts_generation_latency": None,
                "avg_tts_playback_duration": None,
                "avg_total_latency": None,
            }

        # Aggregate metrics
        transcription_latencies = [
            m["transcription_latency"]
            for m in self._completed_metrics
            if m["transcription_latency"] is not None
        ]
        llm_latencies = [
            m["llm_latency"]
            for m in self._completed_metrics
            if m["llm_latency"] is not None
        ]
        tts_gen_latencies = [
            m["tts_generation_latency"]
            for m in self._completed_metrics
            if m["tts_generation_latency"] is not None
        ]
        tts_playback_durations = [
            m["tts_playback_duration"]
            for m in self._completed_metrics
            if m["tts_playback_duration"] is not None
        ]
        total_latencies = [
            m["total_latency"]
            for m in self._completed_metrics
            if m["total_latency"] is not None
        ]

        return {
            "total_conversations": len(self._completed_metrics),
            "avg_transcription_latency": (
                sum(transcription_latencies) / len(transcription_latencies)
                if transcription_latencies else None
            ),
            "avg_llm_latency": (
                sum(llm_latencies) / len(llm_latencies)
                if llm_latencies else None
            ),
            "avg_tts_generation_latency": (
                sum(tts_gen_latencies) / len(tts_gen_latencies)
                if tts_gen_latencies else None
            ),
            "avg_tts_playback_duration": (
                sum(tts_playback_durations) / len(tts_playback_durations)
                if tts_playback_durations else None
            ),
            "avg_total_latency": (
                sum(total_latencies) / len(total_latencies)
                if total_latencies else None
            ),
        }

    # Internal Methods

    def _cleanup_old_conversations(self):
        """Remove oldest conversations if exceeding max limit."""
        while len(self._conversation_metrics) > self._max_conversations:
            # OrderedDict.popitem(last=False) removes oldest item
            self._conversation_metrics.popitem(last=False)

    async def _emit_performance_metrics(self, conversation_id: str, summary: Dict[str, Any]):
        """Emit DEBUG_PERFORMANCE events for completed conversation."""
        # Emit individual stage metrics
        if summary["transcription_latency"] is not None:
            await self.emit(
                EventTopics.DEBUG_PERFORMANCE,
                {
                    "metric_name": "transcription_latency",
                    "value": summary["transcription_latency"],
                    "unit": "seconds",
                    "component": "pipeline",
                    "details": {"conversation_id": conversation_id},
                }
            )

        if summary["llm_latency"] is not None:
            await self.emit(
                EventTopics.DEBUG_PERFORMANCE,
                {
                    "metric_name": "llm_latency",
                    "value": summary["llm_latency"],
                    "unit": "seconds",
                    "component": "pipeline",
                    "details": {"conversation_id": conversation_id},
                }
            )

        if summary["tts_generation_latency"] is not None:
            await self.emit(
                EventTopics.DEBUG_PERFORMANCE,
                {
                    "metric_name": "tts_generation_latency",
                    "value": summary["tts_generation_latency"],
                    "unit": "seconds",
                    "component": "pipeline",
                    "details": {"conversation_id": conversation_id},
                }
            )

        if summary["total_latency"] is not None:
            await self.emit(
                EventTopics.DEBUG_PERFORMANCE,
                {
                    "metric_name": "total_pipeline_latency",
                    "value": summary["total_latency"],
                    "unit": "seconds",
                    "component": "pipeline",
                    "details": {"conversation_id": conversation_id},
                }
            )

    async def handle_latency_command(self, payload: Dict[str, Any]) -> None:
        """
        Handle CLI commands for latency reporting.

        Supported commands:
        - debug latency: Show aggregate latency report
        - debug latency conversation <id>: Show specific conversation metrics
        - debug latency reset: Clear all stored metrics

        Args:
            payload: Command payload with 'args' containing command arguments
        """
        try:
            # Extract command arguments
            args = payload.get("args", [])

            # If no subcommand or just "latency", show aggregate report
            if len(args) == 0 or (len(args) == 1 and args[0] == "latency"):
                report = self.get_latency_report()

                if report["total_conversations"] == 0:
                    await self.emit(EventTopics.CLI_RESPONSE, {
                        "message": "No latency data available yet. Interact with the system to collect metrics.",
                        "is_error": False
                    })
                    return

                # Format the report
                msg_lines = [
                    "=== Pipeline Latency Report ===",
                    f"Total Conversations: {report['total_conversations']}",
                    ""
                ]

                if report["avg_transcription_latency"] is not None:
                    msg_lines.append(f"Avg Transcription Latency: {report['avg_transcription_latency']:.3f}s")

                if report["avg_llm_latency"] is not None:
                    msg_lines.append(f"Avg LLM Latency: {report['avg_llm_latency']:.3f}s")

                if report["avg_tts_generation_latency"] is not None:
                    msg_lines.append(f"Avg TTS Generation Latency: {report['avg_tts_generation_latency']:.3f}s")

                if report["avg_tts_playback_duration"] is not None:
                    msg_lines.append(f"Avg TTS Playback Duration: {report['avg_tts_playback_duration']:.3f}s")

                if report["avg_total_latency"] is not None:
                    msg_lines.append("")
                    msg_lines.append(f"Avg Total Pipeline Latency: {report['avg_total_latency']:.3f}s")

                await self.emit(EventTopics.CLI_RESPONSE, {
                    "message": "\n".join(msg_lines),
                    "is_error": False
                })
                return

            # Handle "debug latency conversation <id>" command
            elif len(args) >= 2 and args[0] == "latency" and args[1] == "conversation":
                if len(args) < 3:
                    await self.emit(EventTopics.CLI_RESPONSE, {
                        "message": "Usage: debug latency conversation <conversation_id>",
                        "is_error": True
                    })
                    return

                conversation_id = args[2]
                summary = self.get_conversation_summary(conversation_id)

                if summary is None:
                    await self.emit(EventTopics.CLI_RESPONSE, {
                        "message": f"No data found for conversation: {conversation_id}",
                        "is_error": True
                    })
                    return

                # Format conversation summary
                msg_lines = [
                    f"=== Conversation {conversation_id} ===",
                    ""
                ]

                if summary["transcription_latency"] is not None:
                    msg_lines.append(f"Transcription: {summary['transcription_latency']:.3f}s")
                else:
                    msg_lines.append("Transcription: N/A")

                if summary["llm_latency"] is not None:
                    msg_lines.append(f"LLM Processing: {summary['llm_latency']:.3f}s")
                else:
                    msg_lines.append("LLM Processing: N/A")

                if summary["tts_generation_latency"] is not None:
                    msg_lines.append(f"TTS Generation: {summary['tts_generation_latency']:.3f}s")
                else:
                    msg_lines.append("TTS Generation: N/A")

                if summary["tts_playback_duration"] is not None:
                    msg_lines.append(f"TTS Playback: {summary['tts_playback_duration']:.3f}s")
                else:
                    msg_lines.append("TTS Playback: N/A")

                if summary["total_latency"] is not None:
                    msg_lines.append("")
                    msg_lines.append(f"Total: {summary['total_latency']:.3f}s")
                else:
                    msg_lines.append("")
                    msg_lines.append("Total: N/A")

                await self.emit(EventTopics.CLI_RESPONSE, {
                    "message": "\n".join(msg_lines),
                    "is_error": False
                })
                return

            # Handle "debug latency reset" command
            elif len(args) >= 2 and args[0] == "latency" and args[1] == "reset":
                conversation_count = len(self._conversation_metrics)
                completed_count = len(self._completed_metrics)

                self._conversation_metrics.clear()
                self._completed_metrics.clear()

                await self.emit(EventTopics.CLI_RESPONSE, {
                    "message": f"Cleared {conversation_count} active and {completed_count} completed conversation metrics.",
                    "is_error": False
                })
                return

            # Unknown subcommand
            else:
                await self.emit(EventTopics.CLI_RESPONSE, {
                    "message": (
                        "Usage:\n"
                        "  debug latency - Show aggregate latency report\n"
                        "  debug latency conversation <id> - Show specific conversation\n"
                        "  debug latency reset - Clear all metrics"
                    ),
                    "is_error": True
                })

        except Exception as e:
            error_msg = f"Error processing latency command: {str(e)}"
            self.logger.error(error_msg)
            await self.emit(EventTopics.CLI_RESPONSE, {
                "message": error_msg,
                "is_error": True
            })
