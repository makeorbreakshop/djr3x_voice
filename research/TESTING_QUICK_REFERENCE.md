# DJ R3X Testing - Quick Reference Guide

**Date:** 2025-11-19

## What We Do Well

- ✅ Three-tier testing (unit/integration/performance)
- ✅ pytest-asyncio with proper event loop management
- ✅ Custom EventSynchronizer for async event testing
- ✅ Comprehensive mocks for external APIs
- ✅ Proper resource cleanup and monitoring

## Critical Gaps

| Gap | Impact | Priority | Effort |
|-----|--------|----------|--------|
| No real-time event monitoring | Hard to debug live system | HIGH | 1-2 days |
| No E2E tests with real APIs | Miss integration bugs | HIGH | 2-3 days |
| No distributed tracing | Can't diagnose latency issues | MEDIUM | 3-5 days |
| No voice quality metrics | Unknown ASR/TTS performance | MEDIUM | 2-3 days |
| Manual Arduino testing | Can't catch LED bugs early | LOW | 3-5 days |
| No visual system docs | Hard to onboard developers | LOW | 1-2 days |

## Recommended Tools

### Must-Have (Implement Now)
1. **Streamlit Event Dashboard**
   - Purpose: Real-time event stream visualization
   - Install: `pip install streamlit`
   - Usage: `streamlit run tools/event_dashboard.py`
   - Benefit: Immediately see what's happening in the system

2. **E2E Test Suite**
   - Purpose: Test with real Deepgram/Claude/ElevenLabs APIs
   - Framework: pytest with `@pytest.mark.e2e` marker
   - Location: `tests/e2e/test_voice_pipeline_e2e.py`
   - Benefit: Catch API integration bugs before production

3. **Jaeger Distributed Tracing**
   - Purpose: Visualize latency across services
   - Install: Docker Compose with Jaeger backend
   - Integration: OpenTelemetry Python SDK
   - Benefit: Pinpoint slow services in conversation flow

### Nice-to-Have (Later)
4. **Voice Quality Metrics**
   - Tool: `jiwer` for WER calculation
   - Metrics: Word Error Rate, latency percentiles, intent accuracy
   - Benefit: Quantify voice pipeline quality

5. **Hardware-in-the-Loop Testing**
   - Tool: Analog Discovery 2 ($279)
   - Purpose: Automated Arduino LED validation
   - Benefit: Catch hardware issues in CI

## Quick Start: Event Dashboard

### Step 1: Create Dashboard Script
```python
# tools/event_dashboard.py
import streamlit as st
from cantina_os.event_bus import EventBus
import asyncio

st.title("DJ R3X Event Monitor")

# Subscribe to all events
event_log = st.empty()
events = []

def on_event(event_name, payload):
    events.append({
        "name": event_name,
        "payload": payload,
        "timestamp": time.time()
    })
    # Update display
    event_log.dataframe(events[-50:])  # Last 50 events

# Start listening
event_bus.on_any(on_event)
```

### Step 2: Run Dashboard
```bash
cd /home/user/djr3x_voice
../venv/bin/python -m streamlit run tools/event_dashboard.py
```

### Step 3: Open Browser
Navigate to `http://localhost:8501` to see live events.

## Quick Start: E2E Testing

### Step 1: Create E2E Test File
```python
# tests/e2e/test_voice_pipeline_e2e.py
import pytest
import os

@pytest.mark.e2e
@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="Requires real API keys"
)
async def test_full_voice_interaction():
    """Test: User says 'play jazz' → Music plays"""

    # 1. Send test audio to Deepgram (real API)
    audio = load_test_audio("play_jazz.wav")
    transcription = await real_deepgram.transcribe(audio)
    assert "jazz" in transcription.text.lower()

    # 2. Send to Claude (real API)
    response = await real_claude.chat(transcription.text)
    assert response.tool_calls[0].name == "play_music"

    # 3. Generate TTS (real API)
    audio = await real_elevenlabs.synthesize(response.text)
    assert len(audio) > 0

    # Verify total latency
    assert total_time < 8.0
```

### Step 2: Run E2E Tests
```bash
# Requires API keys in .env
pytest tests/e2e/ -m e2e -v
```

## Quick Start: Jaeger Tracing

### Step 1: Start Jaeger Backend
```bash
# docker-compose.yml
version: '3'
services:
  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"  # UI
      - "6831:6831/udp"  # Agent
```

```bash
docker-compose up -d
```

### Step 2: Install OpenTelemetry
```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-jaeger
```

### Step 3: Add Tracing to BaseService
```python
# cantina_os/core/base_service.py
from opentelemetry import trace
from opentelemetry.exporter.jaeger import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider

# Setup once at startup
trace.set_tracer_provider(TracerProvider())
jaeger_exporter = JaegerExporter(agent_host_name="localhost", agent_port=6831)
trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(jaeger_exporter))

class BaseService:
    def __init__(self, ...):
        self.tracer = trace.get_tracer(self.service_name)

    async def handle_event(self, event_name, payload):
        with self.tracer.start_as_current_span(f"handle_{event_name}"):
            # Process event
            pass
```

### Step 4: View Traces
1. Open `http://localhost:16686` (Jaeger UI)
2. Search for service: "DeepgramDirectMicService"
3. View trace timeline showing all services involved

## Testing Cheat Sheet

### Run Tests by Type
```bash
# Unit tests only (fast, mocked)
pytest tests/unit/ -v

# Integration tests (services together, mocked APIs)
pytest tests/integration/ -v

# E2E tests (real APIs, requires keys)
pytest tests/e2e/ -m e2e -v

# Performance tests
pytest tests/performance/ -v

# All tests except E2E
pytest tests/ -m "not e2e" -v
```

### Debug Failing Tests
```bash
# Show print statements
pytest tests/integration/test_audio_pipeline.py -v -s

# Stop on first failure
pytest tests/ -x

# Run specific test
pytest tests/integration/test_audio_pipeline.py::test_music_playback_basic -v
```

### Test with Real Event Monitoring
```bash
# Terminal 1: Start event dashboard
streamlit run tools/event_dashboard.py

# Terminal 2: Run tests
pytest tests/integration/ -v

# Watch events in browser as tests run
```

## Voice Quality Metrics

### Calculate Word Error Rate (WER)
```python
from jiwer import wer

ground_truth = "play some jazz music"
transcription = "play some jaz music"  # Typo from ASR

error_rate = wer(ground_truth, transcription)
print(f"WER: {error_rate:.2%}")  # Output: WER: 25.00%
```

### Measure Latency Percentiles
```python
import numpy as np

latencies = [4.2, 3.8, 5.1, 4.5, 3.9, ...]  # seconds

p50 = np.percentile(latencies, 50)  # Median
p95 = np.percentile(latencies, 95)  # 95th percentile
p99 = np.percentile(latencies, 99)  # 99th percentile

print(f"P50: {p50:.2f}s, P95: {p95:.2f}s, P99: {p99:.2f}s")
```

## Arduino Testing Patterns

### Basic Serial Protocol Test
```python
def test_arduino_led_command():
    """Test sending LED command to Arduino."""

    # Send command
    command = json.dumps({"pattern": "SPEAKING", "color": [255, 0, 0]})
    serial_port.write(command.encode() + b'\n')

    # Read response
    response = serial_port.readline()
    response_data = json.loads(response)

    assert response_data["status"] == "ok"
    assert response_data["pattern"] == "SPEAKING"
```

### Loopback Test
```python
def test_arduino_serial_loopback():
    """Verify serial connection integrity."""

    test_message = b"TEST_MESSAGE"
    serial_port.write(test_message)

    # Arduino should echo back
    response = serial_port.read(len(test_message))
    assert response == test_message
```

## ROS-Inspired Tools We Should Adopt

| ROS Tool | Purpose | DJ R3X Equivalent | Status |
|----------|---------|-------------------|--------|
| rostopic echo | Monitor topic in real-time | Event dashboard | ❌ Need to build |
| rqt_graph | Visualize node graph | Service diagram | ❌ Need to create |
| rosbag record | Record messages | Event recording | ❌ Not implemented |
| rostest | Integration testing | pytest integration tests | ✅ Already have |
| roslaunch | Launch nodes | CantinaOS startup | ✅ Already have |

## Implementation Roadmap

### Week 1: Event Visibility
- [ ] Create Streamlit event dashboard
- [ ] Add real-time filtering and search
- [ ] Show conversation_id chains
- [ ] Deploy to internal network

### Week 2: E2E Testing
- [ ] Create `tests/e2e/` directory
- [ ] Add E2E test for full voice pipeline
- [ ] Add E2E test for DJ mode transition
- [ ] Configure CI to run E2E on release branches

### Week 3: Distributed Tracing
- [ ] Set up Jaeger backend (Docker)
- [ ] Install OpenTelemetry in BaseService
- [ ] Add spans to all event emissions
- [ ] Document how to use Jaeger UI

### Week 4: Quality Metrics
- [ ] Install jiwer for WER calculation
- [ ] Create test audio dataset with ground truth
- [ ] Add WER test to CI
- [ ] Add latency percentile tracking

### Month 2: Hardware & Docs
- [ ] Improve Arduino serial testing
- [ ] Create Mermaid system diagrams
- [ ] (Optional) Purchase Analog Discovery 2
- [ ] (Optional) Automate LED pattern validation

## Resources

- **Full Research Document:** `/home/user/djr3x_voice/research/TESTING_FRAMEWORKS_RESEARCH.md`
- **Current Tests:** `/home/user/djr3x_voice/cantina_os/tests/`
- **Test Utilities:** `/home/user/djr3x_voice/cantina_os/tests/utils/`

## Getting Help

### Key Testing Files to Review
1. `tests/conftest.py` - Pytest fixtures and configuration
2. `tests/utils/event_synchronizer.py` - Async event testing utility
3. `tests/integration/test_audio_pipeline.py` - Example integration test
4. `tests/unit/test_gpt_service.py` - Example unit test

### Common Issues

**Problem:** Tests hang or timeout
**Solution:** Increase timeout in EventSynchronizer or add more grace periods

**Problem:** Events arrive out of order
**Solution:** Use `wait_for_events(..., in_order=True)` in EventSynchronizer

**Problem:** Mock not working
**Solution:** Check that mock is registered in `conftest.py` with correct scope

**Problem:** Real API call in test
**Solution:** Verify all external APIs are patched in `conftest.py` `mock_external_apis` fixture

## Contact

For questions about testing approaches, consult:
- ROS Testing Wiki: http://wiki.ros.org/rostest
- pytest-asyncio docs: https://pytest-asyncio.readthedocs.io/
- This research document: `research/TESTING_FRAMEWORKS_RESEARCH.md`
