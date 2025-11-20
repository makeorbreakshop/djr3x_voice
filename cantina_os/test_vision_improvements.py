#!/usr/bin/env python3
"""
Test script to verify vision service improvements.
This simulates face detection patterns to ensure:
1. New person triggers immediate scene capture
2. Same person re-detection within cooldown doesn't trigger capture
3. Person exit after short duration doesn't trigger capture
4. Person exit after long duration does trigger capture
"""

import asyncio
import time
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add cantina_os to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cantina_os.services.vision_service import VisionService
from cantina_os.core.event_topics import EventTopics


class TestVisionImprovements:
    def __init__(self):
        self.scene_captures = []
        self.person_detections = []
        self.person_exits = []

    async def test_scenario(self):
        """Test the improved vision service logic."""

        # Create mock event bus
        mock_event_bus = Mock()
        mock_event_bus.emit = self.track_event
        mock_event_bus.on = Mock()

        # Create vision service with mocked components
        config = {
            "camera_index": 0,
            "enable_continuous_monitoring": False,  # Disable continuous monitoring for testing
            "monitoring_fps": 5,
            "face_confidence_threshold": 0.6
        }

        # Mock the Anthropic client
        with patch('cantina_os.services.vision_service.Anthropic') as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.return_value = mock_client

            # Set up environment
            os.environ['ANTHROPIC_API_KEY'] = 'test-key'

            # Create service
            service = VisionService(mock_event_bus, config)

            # Mock the analyze_scene method to avoid actual API calls
            async def mock_analyze_scene(frame, prompt):
                return "Test scene description"
            service._analyze_scene = mock_analyze_scene

            # Initialize service
            await service._start()

            print("\n=== Testing Vision Service Improvements ===\n")

            # Test 1: Brandon detected for first time (should capture)
            print("Test 1: Brandon detected (first time)")
            frame = Mock()  # Mock frame
            await service._handle_person_detection("Brandon", 0.7, frame)
            await asyncio.sleep(0.1)

            # Test 2: Brandon exits quickly (< 30 seconds, should NOT capture)
            print("\nTest 2: Brandon exits after 10 seconds")
            service._person_detection_time = time.time() - 10  # Simulate 10 seconds ago
            service._no_person_frames = 10  # Trigger exit
            await service._handle_person_detection(None, 0.0, frame)
            await asyncio.sleep(0.1)

            # Test 3: Brandon re-detected within cooldown (should NOT capture)
            print("\nTest 3: Brandon re-detected after 20 seconds")
            # Simulate 20 seconds since last capture
            service._person_last_scene_capture["Brandon"] = time.time() - 20
            await service._handle_person_detection("Brandon", 0.7, frame)
            await asyncio.sleep(0.1)

            # Test 4: Different person (Sarah) detected (should capture immediately)
            print("\nTest 4: Sarah detected (new person)")
            await service._handle_person_detection("Sarah", 0.8, frame)
            await asyncio.sleep(0.1)

            # Test 5: Sarah exits after long duration (> 30 seconds, should capture)
            print("\nTest 5: Sarah exits after 45 seconds")
            service._person_detection_time = time.time() - 45  # Simulate 45 seconds ago
            service._no_person_frames = 10  # Trigger exit
            await service._handle_person_detection(None, 0.0, frame)
            await asyncio.sleep(0.1)

            # Test 6: Brandon re-detected after cooldown expired (should capture)
            print("\nTest 6: Brandon re-detected after cooldown (70 seconds)")
            service._person_last_scene_capture["Brandon"] = time.time() - 70
            await service._handle_person_detection("Brandon", 0.7, frame)
            await asyncio.sleep(0.1)

            # Print results
            print("\n=== Test Results ===\n")
            print(f"Total scene captures: {len(self.scene_captures)}")
            print(f"Expected: 4 (Brandon first, Sarah first, Sarah exit, Brandon after cooldown)")

            for i, capture in enumerate(self.scene_captures, 1):
                reason = capture.get('metadata', {}).get('capture_reason', 'unknown')
                print(f"{i}. {reason}")

            print(f"\nPerson detections: {len(self.person_detections)}")
            print(f"Person exits: {len(self.person_exits)}")

            # Verify expectations
            assert len(self.scene_captures) == 4, f"Expected 4 scene captures, got {len(self.scene_captures)}"
            assert len(self.person_detections) == 4, f"Expected 4 person detections, got {len(self.person_detections)}"
            assert len(self.person_exits) == 2, f"Expected 2 person exits, got {len(self.person_exits)}"

            print("\n✅ All tests passed! Vision improvements working correctly.")

            # Clean up
            await service._stop()

    def track_event(self, event_name, payload):
        """Track emitted events for verification."""
        if event_name == EventTopics.VISION_SCENE_CAPTURED:
            self.scene_captures.append(payload)
            print(f"  → Scene captured: {payload.get('metadata', {}).get('capture_reason', 'unknown')}")
        elif event_name == EventTopics.VISION_PERSON_DETECTED:
            self.person_detections.append(payload)
            print(f"  → Person detected: {payload.get('name')} (confidence: {payload.get('confidence')})")
        elif event_name == EventTopics.VISION_PERSON_EXITED:
            self.person_exits.append(payload)
            duration = payload.get('duration_seconds', 0)
            print(f"  → Person exited: {payload.get('name')} (duration: {duration:.1f}s)")


async def main():
    tester = TestVisionImprovements()
    await tester.test_scenario()


if __name__ == "__main__":
    asyncio.run(main())