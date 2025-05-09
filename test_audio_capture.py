import sounddevice as sd
import numpy as np
import time
import matplotlib.pyplot as plt
import os

def test_audio_capture():
    """Test basic audio capture and playback."""
    
    # Audio settings
    fs = 16000  # Sample rate
    duration = 5  # seconds
    channels = 1
    
    print("\n===== AUDIO CAPTURE TEST =====")
    print(f"Recording {duration} seconds of audio at {fs}Hz with {channels} channel(s)")
    print("Please speak into your microphone...")
    
    # Record audio
    recording = sd.rec(int(fs * duration), samplerate=fs, channels=channels, dtype='float32')
    
    # Show real-time levels while recording
    start_time = time.time()
    while time.time() - start_time < duration:
        current_frame = int((time.time() - start_time) * fs)
        if current_frame < len(recording):
            current_level = np.max(np.abs(recording[:current_frame])) if current_frame > 0 else 0
            if not np.isfinite(current_level):
                current_level = 0.0
            bars = min(int(current_level * 50), 50)  # Cap at maximum 50 bars
            print(f"\rLevel: {'#' * bars}{' ' * (50-bars)} | {current_level:.4f}", end="")
        time.sleep(0.1)
    
    sd.wait()  # Wait until recording is finished
    print("\nRecording complete!")
    
    # Analyze recording
    max_amplitude = np.max(np.abs(recording))
    rms_level = np.sqrt(np.mean(recording**2))
    
    print("\n===== AUDIO ANALYSIS =====")
    print(f"Max amplitude: {max_amplitude:.4f}")
    print(f"RMS level: {rms_level:.4f}")
    print(f"Is signal present: {'YES' if max_amplitude > 0.01 else 'NO - Very quiet or no microphone'}")
    
    # Play back the recording
    print("\n===== AUDIO PLAYBACK =====")
    print("Playing back the recording...")
    sd.play(recording, fs)
    sd.wait()
    
    # Save audio data for inspection
    print("\n===== SAVING DEBUG DATA =====")
    np.save("debug_audio.npy", recording)
    print("Saved recording to debug_audio.npy")
    
    # Plot waveform
    try:
        plt.figure(figsize=(10, 4))
        plt.plot(np.linspace(0, duration, len(recording)), recording)
        plt.title("Audio Waveform")
        plt.xlabel("Time (s)")
        plt.ylabel("Amplitude")
        plt.savefig("audio_waveform.png")
        print("Saved waveform visualization to audio_waveform.png")
    except Exception as e:
        print(f"Could not generate waveform plot: {e}")
    
    return max_amplitude, rms_level

def test_microphone_devices():
    """List and test available microphone devices."""
    print("\n===== AVAILABLE AUDIO DEVICES =====")
    devices = sd.query_devices()
    
    # Print all devices
    print("All audio devices:")
    for i, device in enumerate(devices):
        print(f"{i}: {device['name']} (in: {device['max_input_channels']}, out: {device['max_output_channels']})")
    
    # Find default input device
    default_input = sd.query_devices(kind='input')
    print(f"\nDefault input device: {default_input['name']}")
    
    # List only input devices
    print("\nAvailable input devices:")
    input_devices = []
    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            input_devices.append((i, device))
            print(f"{i}: {device['name']} ({device['max_input_channels']} channels)")
    
    return input_devices

def main():
    """Run all audio tests."""
    print("DJ R3X Voice - Audio Capture Test Utility")
    print("=========================================")
    
    # Test available microphones
    input_devices = test_microphone_devices()
    
    # Test with default device first
    print("\nTesting with default input device...")
    max_amp, rms = test_audio_capture()
    
    # If signal is very low, offer to try other devices
    if max_amp < 0.01 and len(input_devices) > 1:
        print("\n‼️ Very low audio signal detected with default device.")
        try_another = input("Would you like to try another input device? (y/n): ").lower() == 'y'
        
        if try_another:
            device_idx = int(input(f"Enter device number to test (0-{len(input_devices)-1}): "))
            if 0 <= device_idx < len(devices):
                print(f"\nTesting with device {device_idx}: {devices[device_idx]['name']}...")
                with sd.InputStream(device=device_idx):  # Just to set the device
                    max_amp, rms = test_audio_capture()
    
    print("\n===== TEST SUMMARY =====")
    if max_amp > 0.01:
        print("✅ Audio capture test PASSED - Signal detected")
    else:
        print("❌ Audio capture test FAILED - No significant audio signal detected")
        print("   Possible issues:")
        print("   - Microphone not connected or enabled")
        print("   - Wrong input device selected")
        print("   - Microphone permission denied")
        print("   - Microphone volume too low")
    
    print("\nTest complete!")

if __name__ == "__main__":
    main() 