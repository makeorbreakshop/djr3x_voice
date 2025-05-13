#!/usr/bin/env python3
"""
Microphone Signal Test

This script directly tests microphone input to verify signal levels are appropriate.

Usage:
python test_mic_signal.py
"""

import pyaudio
import numpy as np
import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import threading
import argparse

class MicrophoneSignalTest:
    def __init__(self, sample_rate=16000, chunk_size=1024, channels=1, dtype=pyaudio.paInt16):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.format = dtype
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.running = False
        self.lock = threading.Lock()
        self.signal_data = []
        self.max_samples = 100  # Number of samples to keep
        self.rms_values = []
        self.max_amplitude = 0
        
    def start(self):
        """Start audio capture."""
        print(f"Starting microphone capture at {self.sample_rate}Hz, {self.channels} channel(s)")
        
        # List available input devices
        print("\nAvailable audio input devices:")
        for i in range(self.p.get_device_count()):
            info = self.p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:  # Only show input devices
                print(f"  {i}: {info['name']} (channels: {info['maxInputChannels']})")
        print()
        
        # Open PyAudio stream
        self.stream = self.p.open(
            format=self.format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=self._callback
        )
        
        self.running = True
        print("Microphone capture started. Recording audio...")

    def _callback(self, in_data, frame_count, time_info, status):
        """Process audio data from microphone."""
        if status:
            print(f"Stream status: {status}")
            
        # Convert buffer to numpy array
        audio_data = np.frombuffer(in_data, dtype=np.int16)
        
        # Calculate RMS (loudness)
        rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
        
        # Calculate max amplitude
        max_amp = np.max(np.abs(audio_data))
        
        with self.lock:
            self.max_amplitude = max(self.max_amplitude, max_amp)
            self.rms_values.append(rms)
            if len(self.rms_values) > self.max_samples:
                self.rms_values.pop(0)
                
            # Store raw signal data for plotting
            self.signal_data.append(audio_data)
            if len(self.signal_data) > 5:  # Keep only recent chunks
                self.signal_data.pop(0)
        
        # Check if signal is strong enough
        if rms > 100:  # Adjust threshold as needed
            print(f"Signal detected! RMS: {rms:.1f}, Max amplitude: {max_amp}")
        
        return (in_data, pyaudio.paContinue)
    
    def get_stats(self):
        """Get current audio statistics."""
        with self.lock:
            current_rms = self.rms_values[-1] if self.rms_values else 0
            signal_present = current_rms > 50  # Threshold for "signal present"
            return {
                "rms": current_rms,
                "max_amplitude": self.max_amplitude,
                "signal_present": signal_present
            }

    def stop(self):
        """Stop audio capture."""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.running = False
        self.p.terminate()
        print("\nMicrophone capture stopped")
        
        # Print summary
        if self.rms_values:
            avg_rms = np.mean(self.rms_values)
            print(f"\nAudio Summary:")
            print(f"  Average RMS (loudness): {avg_rms:.1f}")
            print(f"  Maximum amplitude: {self.max_amplitude}")
            print(f"  Signal present: {'YES' if avg_rms > 50 else 'NO'}")
            
            if avg_rms < 50:
                print("\n⚠️ WARNING: Audio signal is very weak. Check microphone and volume settings.")
            elif self.max_amplitude > 30000:
                print("\n⚠️ WARNING: Audio signal may be clipping. Consider reducing microphone gain.")
            else:
                print("\n✅ Audio signal levels look good!")
        
    def setup_plot(self):
        """Set up real-time audio signal plot."""
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(10, 8))
        self.fig.suptitle('Microphone Signal Analysis', fontsize=16)
        
        # Signal waveform plot
        self.line, = self.ax1.plot([], [], lw=1)
        self.ax1.set_ylabel('Amplitude')
        self.ax1.set_ylim(-32768, 32768)
        self.ax1.set_xlim(0, self.chunk_size)
        self.ax1.set_title('Audio Waveform')
        self.ax1.grid(True)
        
        # RMS history plot
        self.rms_line, = self.ax2.plot([], [], lw=2)
        self.ax2.set_xlabel('Time')
        self.ax2.set_ylabel('RMS Level')
        self.ax2.set_ylim(0, 2000)  # Adjust as needed
        self.ax2.set_xlim(0, self.max_samples)
        self.ax2.set_title('Audio Level History')
        self.ax2.grid(True)
        
        # Text annotation for stats
        self.stats_text = self.fig.text(0.02, 0.02, '', fontsize=10)
        
        # Create animation
        self.ani = FuncAnimation(
            self.fig, self.update_plot, interval=100, 
            blit=False, cache_frame_data=False
        )
        
    def update_plot(self, frame):
        """Update plot with new audio data."""
        with self.lock:
            if not self.signal_data:
                return self.line, self.rms_line, self.stats_text
                
            # Update waveform plot
            latest_data = self.signal_data[-1]
            x = np.arange(len(latest_data))
            self.line.set_data(x, latest_data)
            
            # Update RMS history plot
            rms_x = np.arange(len(self.rms_values))
            self.rms_line.set_data(rms_x, self.rms_values)
            
            # Update stats text
            stats = self.get_stats()
            stats_str = (
                f"Current RMS: {stats['rms']:.1f}\n"
                f"Max amplitude: {stats['max_amplitude']}\n"
                f"Signal present: {'YES' if stats['signal_present'] else 'NO'}"
            )
            self.stats_text.set_text(stats_str)
            
        return self.line, self.rms_line, self.stats_text

def main(duration=10, visualize=True):
    """Run the microphone signal test."""
    mic_test = MicrophoneSignalTest()
    
    try:
        mic_test.start()
        
        if visualize:
            mic_test.setup_plot()
            plt.show()
        else:
            # Just print stats periodically
            start_time = time.time()
            while time.time() - start_time < duration:
                stats = mic_test.get_stats()
                print(f"RMS: {stats['rms']:.1f}, Signal: {'YES' if stats['signal_present'] else 'NO'}")
                time.sleep(1)
    
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    finally:
        mic_test.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test microphone signal levels")
    parser.add_argument('--duration', type=int, default=10, help='Test duration in seconds (if not visualizing)')
    parser.add_argument('--no-visual', action='store_true', help='Disable visualization')
    args = parser.parse_args()
    
    main(duration=args.duration, visualize=not args.no_visual) 