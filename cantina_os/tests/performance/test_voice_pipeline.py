"""Performance tests for the voice processing pipeline."""
import pytest
import asyncio
from typing import Dict, Any
from .metrics import PerformanceMetrics
from ..mocks.deepgram_mock import DeepgramMock
from ..mocks.openai_mock import OpenAIMock
from ..mocks.elevenlabs_mock import ElevenLabsMock

class VoicePipelinePerformanceTest:
    """Base class for voice pipeline performance testing."""
    
    def __init__(self) -> None:
        """Initialize the performance test."""
        self.metrics = PerformanceMetrics()
        self.transcripts_received = 0
        self.responses_received = 0
        self.chunks_received = 0
        self.audio_started = 0
        self.audio_completed = 0
        
    async def setup_pipeline(
        self,
        deepgram_mock: DeepgramMock,
        openai_mock: OpenAIMock,
        elevenlabs_mock: ElevenLabsMock
    ) -> None:
        """Set up the voice processing pipeline with mocks."""
        await self.metrics.start_monitoring(interval=0.1)  # 100ms sampling
        
    async def teardown_pipeline(self) -> None:
        """Clean up the pipeline and stop monitoring."""
        await self.metrics.stop_monitoring()
        
    def on_transcript(self, data: Dict[str, Any]) -> None:
        """Handle transcript events."""
        self.transcripts_received += 1
        
    def on_chunk(self, data: Dict[str, Any]) -> None:
        """Handle GPT response chunks."""
        self.chunks_received += 1
        
    def on_response(self, data: Dict[str, Any]) -> None:
        """Handle complete AI responses."""
        self.responses_received += 1
        
    def on_audio_start(self) -> None:
        """Handle audio playback start."""
        self.audio_started += 1
        
    def on_audio_complete(self) -> None:
        """Handle audio playback completion."""
        self.audio_completed += 1

@pytest.mark.performance
class TestVoicePipelinePerformance:
    """Performance tests for the voice processing pipeline."""
    
    @pytest.fixture
    async def pipeline_test(
        self,
        configured_deepgram_mock: DeepgramMock,
        configured_openai_mock: OpenAIMock,
        configured_elevenlabs_mock: ElevenLabsMock
    ) -> VoicePipelinePerformanceTest:
        """Set up a pipeline test instance."""
        test = VoicePipelinePerformanceTest()
        await test.setup_pipeline(
            configured_deepgram_mock,
            configured_openai_mock,
            configured_elevenlabs_mock
        )
        yield test
        await test.teardown_pipeline()
    
    @pytest.mark.asyncio
    async def test_full_pipeline_latency(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_deepgram_mock: DeepgramMock,
        configured_openai_mock: OpenAIMock,
        configured_elevenlabs_mock: ElevenLabsMock
    ):
        """Test end-to-end pipeline latency including speech synthesis."""
        # Configure callbacks
        configured_deepgram_mock.on_transcript(pipeline_test.on_transcript)
        configured_openai_mock.on_chunk(pipeline_test.on_chunk)
        configured_elevenlabs_mock.on_audio_start(pipeline_test.on_audio_start)
        configured_elevenlabs_mock.on_audio_complete(pipeline_test.on_audio_complete)
        
        # Start voice input
        with pipeline_test.metrics.measure_latency("full_pipeline"):
            # Start streaming
            await configured_deepgram_mock.start_streaming()
            
            # Wait for transcript
            await asyncio.sleep(1.0)
            
            # Process with GPT
            messages = [{"role": "user", "content": "Hello DJ R3X!"}]
            response = await configured_openai_mock.chat_completion(messages, stream=False)
            
            # Generate and play speech
            await configured_elevenlabs_mock.text_to_speech(
                text=response["choices"][0]["message"]["content"],
                voice_id="test_voice"
            )
            
            # Stop streaming
            await configured_deepgram_mock.stop_streaming()
            
        # Get pipeline stats
        pipeline_stats = pipeline_test.metrics.get_latency_stats("full_pipeline")
        
        # Verify performance
        assert pipeline_stats['avg_ms'] < 3000, "End-to-end latency too high"
        assert pipeline_test.transcripts_received > 0, "No transcripts received"
        assert pipeline_test.audio_started > 0, "Audio playback never started"
        assert pipeline_test.audio_completed > 0, "Audio playback never completed"
        
    @pytest.mark.asyncio
    async def test_speech_synthesis_latency(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_elevenlabs_mock: ElevenLabsMock
    ):
        """Test speech synthesis and playback latency."""
        configured_elevenlabs_mock.on_audio_start(pipeline_test.on_audio_start)
        configured_elevenlabs_mock.on_audio_complete(pipeline_test.on_audio_complete)
        
        test_texts = [
            "Hello!",
            "Welcome to the cantina!",
            "Let me play some music for you!"
        ]
        
        for text in test_texts:
            with pipeline_test.metrics.measure_latency("speech_synthesis"):
                await configured_elevenlabs_mock.text_to_speech(
                    text=text,
                    voice_id="test_voice"
                )
                
        # Get synthesis stats
        synthesis_stats = pipeline_test.metrics.get_latency_stats("speech_synthesis")
        
        # Verify performance
        assert synthesis_stats['avg_ms'] < 1000, "Speech synthesis latency too high"
        assert pipeline_test.audio_started == len(test_texts), "Not all audio playbacks started"
        assert pipeline_test.audio_completed == len(test_texts), "Not all audio playbacks completed"
        
    @pytest.mark.asyncio
    async def test_continuous_conversation(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_deepgram_mock: DeepgramMock,
        configured_openai_mock: OpenAIMock,
        configured_elevenlabs_mock: ElevenLabsMock
    ):
        """Test continuous conversation performance."""
        # Configure callbacks
        configured_deepgram_mock.on_transcript(pipeline_test.on_transcript)
        configured_openai_mock.on_chunk(pipeline_test.on_chunk)
        configured_elevenlabs_mock.on_audio_start(pipeline_test.on_audio_start)
        configured_elevenlabs_mock.on_audio_complete(pipeline_test.on_audio_complete)
        
        # Start streaming
        await configured_deepgram_mock.start_streaming()
        
        start_time = asyncio.get_event_loop().time()
        conversation_count = 0
        
        # Run continuous conversation for 30 seconds
        while (asyncio.get_event_loop().time() - start_time) < 30:
            # Process one turn of conversation
            with pipeline_test.metrics.measure_latency("conversation_turn"):
                # Get GPT response
                messages = [{"role": "user", "content": "Hello DJ R3X!"}]
                response = await configured_openai_mock.chat_completion(messages, stream=False)
                
                # Generate and play speech
                await configured_elevenlabs_mock.text_to_speech(
                    text=response["choices"][0]["message"]["content"],
                    voice_id="test_voice"
                )
                
            conversation_count += 1
            await asyncio.sleep(0.5)  # Brief pause between turns
            
        # Stop streaming
        await configured_deepgram_mock.stop_streaming()
        
        # Get conversation turn stats
        turn_stats = pipeline_test.metrics.get_latency_stats("conversation_turn")
        memory_stats = pipeline_test.metrics.get_memory_stats()
        
        # Calculate throughput (conversation turns per second)
        duration = asyncio.get_event_loop().time() - start_time
        throughput = conversation_count / duration
        
        # Verify performance
        assert turn_stats['avg_ms'] < 2000, "Conversation turn latency too high"
        assert throughput >= 0.3, "Conversation throughput too low"  # At least 1 turn every 3 seconds
        assert memory_stats['rss_mb']['max'] < 500, "Memory usage too high"
        assert memory_stats['cpu_percent']['avg'] < 50, "CPU usage too high"
        
    @pytest.mark.asyncio
    async def test_transcription_latency(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_deepgram_mock: DeepgramMock
    ):
        """Test transcription latency under normal conditions."""
        # Configure callbacks
        configured_deepgram_mock.on_transcript(pipeline_test.on_transcript)
        
        # Measure streaming start latency
        with pipeline_test.metrics.measure_latency("streaming_start"):
            await configured_deepgram_mock.start_streaming()
            
        # Let it run for a few seconds
        await asyncio.sleep(3.0)
        
        # Measure streaming stop latency
        with pipeline_test.metrics.measure_latency("streaming_stop"):
            await configured_deepgram_mock.stop_streaming()
            
        # Get latency stats
        start_stats = pipeline_test.metrics.get_latency_stats("streaming_start")
        stop_stats = pipeline_test.metrics.get_latency_stats("streaming_stop")
        
        # Verify performance
        assert start_stats['avg_ms'] < 100, "Streaming start latency too high"
        assert stop_stats['avg_ms'] < 100, "Streaming stop latency too high"
        assert pipeline_test.transcripts_received > 0, "No transcripts received"
        
    @pytest.mark.asyncio
    async def test_gpt_response_latency(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_openai_mock: OpenAIMock
    ):
        """Test GPT response latency."""
        # Configure callbacks
        configured_openai_mock.on_chunk(pipeline_test.on_chunk)
        
        messages = [{"role": "user", "content": "Hello DJ R3X!"}]
        
        # Measure non-streaming completion latency
        with pipeline_test.metrics.measure_latency("completion"):
            response = await configured_openai_mock.chat_completion(messages)
            
        # Measure streaming completion latency
        with pipeline_test.metrics.measure_latency("streaming"):
            async for _ in await configured_openai_mock.chat_completion(messages, stream=True):
                pass
                
        # Get latency stats
        completion_stats = pipeline_test.metrics.get_latency_stats("completion")
        streaming_stats = pipeline_test.metrics.get_latency_stats("streaming")
        
        # Verify performance
        assert completion_stats['avg_ms'] < 500, "Completion latency too high"
        assert streaming_stats['avg_ms'] < 1000, "Streaming latency too high"
        assert pipeline_test.chunks_received > 0, "No chunks received"
        
    @pytest.mark.asyncio
    async def test_end_to_end_latency(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_deepgram_mock: DeepgramMock,
        configured_openai_mock: OpenAIMock
    ):
        """Test end-to-end pipeline latency."""
        # Configure callbacks
        configured_deepgram_mock.on_transcript(pipeline_test.on_transcript)
        configured_openai_mock.on_chunk(pipeline_test.on_chunk)
        
        # Start voice input
        with pipeline_test.metrics.measure_latency("pipeline"):
            # Start streaming
            await configured_deepgram_mock.start_streaming()
            
            # Wait for transcript
            await asyncio.sleep(1.0)
            
            # Process with GPT
            messages = [{"role": "user", "content": "Hello DJ R3X!"}]
            async for _ in await configured_openai_mock.chat_completion(messages, stream=True):
                pass
                
            # Stop streaming
            await configured_deepgram_mock.stop_streaming()
            
        # Get pipeline stats
        pipeline_stats = pipeline_test.metrics.get_latency_stats("pipeline")
        
        # Verify performance
        assert pipeline_stats['avg_ms'] < 2000, "End-to-end latency too high"
        assert pipeline_test.transcripts_received > 0, "No transcripts received"
        assert pipeline_test.chunks_received > 0, "No GPT chunks received"
        
    @pytest.mark.asyncio
    async def test_memory_usage(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_deepgram_mock: DeepgramMock,
        configured_openai_mock: OpenAIMock
    ):
        """Test memory usage during full pipeline operation."""
        # Configure callbacks
        configured_deepgram_mock.on_transcript(pipeline_test.on_transcript)
        configured_openai_mock.on_chunk(pipeline_test.on_chunk)
        
        # Start streaming
        await configured_deepgram_mock.start_streaming()
        
        # Simulate conversation
        for _ in range(5):
            messages = [{"role": "user", "content": "Hello DJ R3X!"}]
            async for _ in await configured_openai_mock.chat_completion(messages, stream=True):
                pass
            await asyncio.sleep(1.0)
            
        # Stop streaming
        await configured_deepgram_mock.stop_streaming()
        
        # Get memory stats
        memory_stats = pipeline_test.metrics.get_memory_stats()
        
        # Verify memory usage
        assert memory_stats['rss_mb']['max'] < 500, "Memory usage too high"
        assert memory_stats['cpu_percent']['avg'] < 50, "CPU usage too high"
        
    @pytest.mark.asyncio
    async def test_pipeline_throughput(
        self,
        pipeline_test: VoicePipelinePerformanceTest,
        configured_deepgram_mock: DeepgramMock,
        configured_openai_mock: OpenAIMock
    ):
        """Test pipeline throughput with continuous conversation."""
        # Configure callbacks
        configured_deepgram_mock.on_transcript(pipeline_test.on_transcript)
        configured_openai_mock.on_chunk(pipeline_test.on_chunk)
        
        # Start streaming
        await configured_deepgram_mock.start_streaming()
        
        start_time = asyncio.get_event_loop().time()
        conversation_count = 0
        
        # Run continuous conversation for 30 seconds
        while (asyncio.get_event_loop().time() - start_time) < 30:
            messages = [{"role": "user", "content": "Hello DJ R3X!"}]
            response = await configured_openai_mock.chat_completion(messages, stream=False)
            pipeline_test.responses_received += 1
            conversation_count += 1
            await asyncio.sleep(0.5)  # Simulate natural conversation pace
            
        # Stop streaming
        await configured_deepgram_mock.stop_streaming()
        
        # Calculate throughput (complete interactions per second)
        duration = asyncio.get_event_loop().time() - start_time
        throughput = conversation_count / duration
        
        # Verify throughput
        assert throughput >= 0.5, "Pipeline throughput too low"  # At least 1 interaction every 2 seconds 