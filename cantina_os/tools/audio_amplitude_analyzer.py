#!/usr/bin/env python3
"""
Audio Amplitude Analyzer for DJ R3X Mouth LED Optimization

This tool analyzes captured PCM audio from ElevenLabs TTS to determine
the optimal amplitude normalization and compression settings for mouth LEDs.

Usage:
    1. Capture audio using capture_tts_audio.py
    2. Run this analyzer on the captured .pcm file
    3. Compare different normalization strategies
"""

import numpy as np
import matplotlib.pyplot as plt
import math
from pathlib import Path
from typing import Tuple, List, Dict
import argparse


class AmplitudeAnalyzer:
    """Analyzes audio amplitude and tests different normalization strategies."""

    def __init__(self, pcm_file: Path, sample_rate: int = 24000, channels: int = 1):
        """
        Initialize analyzer with PCM audio file.

        Args:
            pcm_file: Path to raw PCM audio file (16-bit signed)
            sample_rate: Sample rate in Hz (ElevenLabs uses 24kHz)
            channels: Number of channels (1 = mono)
        """
        self.pcm_file = pcm_file
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = 1024  # Same as ElevenLabs service

        # Load audio
        print(f"Loading audio from {pcm_file}...")
        self.audio_data = np.fromfile(pcm_file, dtype=np.int16)
        duration = len(self.audio_data) / sample_rate
        print(f"  Duration: {duration:.2f} seconds")
        print(f"  Samples: {len(self.audio_data):,}")

    def calculate_rms_timeline(self) -> Tuple[np.ndarray, List[float]]:
        """
        Calculate RMS amplitude for each chunk, exactly like ElevenLabs service.

        Returns:
            (timestamps, rms_db_values)
        """
        timestamps = []
        rms_db_values = []

        MAX_AMPLITUDE = 32768.0  # 16-bit signed max

        for i in range(0, len(self.audio_data), self.chunk_size):
            chunk = self.audio_data[i:i + self.chunk_size]
            if len(chunk) == 0:
                continue

            # Calculate RMS (Root Mean Square)
            rms = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))

            # Convert to dB (logarithmic scale)
            if rms > 0:
                db_value = 20 * math.log10(rms / MAX_AMPLITUDE)
            else:
                db_value = -80  # Silence floor

            timestamps.append(i / self.sample_rate)
            rms_db_values.append(db_value)

        return np.array(timestamps), rms_db_values

    def apply_agc_normalization(
        self,
        rms_db_values: List[float],
        boost: float = 2.0,
        agc_window_size: int = 30,
        min_dynamic_range_db: float = 12.0
    ) -> np.ndarray:
        """
        Apply AGC normalization (current ElevenLabs service approach).

        Args:
            rms_db_values: Raw dB values
            boost: Post-normalization boost multiplier
            agc_window_size: Number of chunks for AGC window (~0.5 seconds at 60Hz)
            min_dynamic_range_db: Minimum dynamic range to prevent over-compression

        Returns:
            Normalized amplitude values (0.0-1.0)
        """
        normalized = []
        recent_rms_values = []

        for db_value in rms_db_values:
            recent_rms_values.append(db_value)
            if len(recent_rms_values) > agc_window_size:
                recent_rms_values.pop(0)

            if len(recent_rms_values) >= 5:  # Need some history
                recent_min_db = min(recent_rms_values)
                recent_max_db = max(recent_rms_values)
                dynamic_range_db = max(min_dynamic_range_db, recent_max_db - recent_min_db)

                # Map current dB relative to recent range
                norm_amp = (db_value - recent_min_db) / dynamic_range_db
                norm_amp = max(0, min(1.0, norm_amp))

                # Apply boost
                norm_amp = min(1.0, norm_amp * boost)
            else:
                # Fallback during first few chunks
                norm_amp = max(0, min(1.0, (db_value + 50) / 40))
                norm_amp = min(1.0, norm_amp * boost)

            normalized.append(norm_amp)

        return np.array(normalized)

    def apply_simple_normalization(
        self,
        rms_db_values: List[float],
        db_floor: float = -50,
        db_ceiling: float = -10,
        boost: float = 1.0
    ) -> np.ndarray:
        """
        Apply simple static range normalization.

        Args:
            rms_db_values: Raw dB values
            db_floor: Minimum dB to map to 0.0
            db_ceiling: Maximum dB to map to 1.0
            boost: Post-normalization boost

        Returns:
            Normalized amplitude values (0.0-1.0)
        """
        normalized = []
        for db_value in rms_db_values:
            norm_amp = (db_value - db_floor) / (db_ceiling - db_floor)
            norm_amp = max(0, min(1.0, norm_amp))
            norm_amp = min(1.0, norm_amp * boost)
            normalized.append(norm_amp)

        return np.array(normalized)

    def apply_python_boost(self, normalized_amp: np.ndarray, boost: float = 8.0) -> np.ndarray:
        """
        Apply Python service boost (simulates eye_light_controller_service).

        Args:
            normalized_amp: Amplitude values from AGC (0.0-1.0)
            boost: Boost multiplier

        Returns:
            Boosted amplitude (0.0-1.0, clipped)
        """
        boosted = normalized_amp * boost
        return np.clip(boosted, 0.0, 1.0)

    def apply_arduino_compression(
        self,
        normalized_amp: np.ndarray,
        compression: str = "double_sqrt"
    ) -> np.ndarray:
        """
        Apply Arduino compression curve.

        Args:
            normalized_amp: Amplitude values (0.0-1.0)
            compression: Compression type ("none", "sqrt", "double_sqrt", "log")

        Returns:
            Compressed amplitude (0.0-1.0)
        """
        if compression == "none":
            return normalized_amp
        elif compression == "sqrt":
            return np.sqrt(normalized_amp)
        elif compression == "double_sqrt":
            return np.sqrt(np.sqrt(normalized_amp))  # x^0.25
        elif compression == "log":
            # Logarithmic compression (gentler than sqrt)
            return np.log10(1 + normalized_amp * 9) / np.log10(10)  # Maps 0-1 to 0-1 logarithmically
        else:
            raise ValueError(f"Unknown compression type: {compression}")

    def calculate_led_brightness(
        self,
        compressed_amp: np.ndarray,
        max_brightness: int = 255
    ) -> np.ndarray:
        """
        Calculate final LED brightness values (0-255).

        Args:
            compressed_amp: Compressed amplitude (0.0-1.0)
            max_brightness: Maximum LED brightness (current Arduino uses 180)

        Returns:
            LED brightness values (0-255)
        """
        return (compressed_amp * max_brightness).astype(int)

    def analyze_distribution(self, values: np.ndarray, label: str = "Values") -> Dict:
        """
        Analyze statistical distribution of values.

        Returns:
            Dictionary with min, max, mean, median, percentiles, etc.
        """
        return {
            "label": label,
            "min": np.min(values),
            "max": np.max(values),
            "mean": np.mean(values),
            "median": np.median(values),
            "std": np.std(values),
            "p10": np.percentile(values, 10),
            "p25": np.percentile(values, 25),
            "p50": np.percentile(values, 50),
            "p75": np.percentile(values, 75),
            "p90": np.percentile(values, 90),
            "p95": np.percentile(values, 95),
            "p99": np.percentile(values, 99),
            "clipped_percent": (np.sum(values >= 254) / len(values) * 100),
            "silent_percent": (np.sum(values <= 5) / len(values) * 100)
        }

    def print_stats(self, stats: Dict):
        """Pretty print statistics."""
        print(f"\n{stats['label']}:")
        print(f"  Range: {stats['min']:.2f} - {stats['max']:.2f}")
        print(f"  Mean: {stats['mean']:.2f}  Median: {stats['median']:.2f}  Std: {stats['std']:.2f}")
        print(f"  Percentiles:")
        print(f"    10%: {stats['p10']:.2f}  25%: {stats['p25']:.2f}  50%: {stats['p50']:.2f}")
        print(f"    75%: {stats['p75']:.2f}  90%: {stats['p90']:.2f}  95%: {stats['p95']:.2f}")
        print(f"  Clipped (≥254): {stats['clipped_percent']:.1f}%")
        print(f"  Silent (≤5): {stats['silent_percent']:.1f}%")

    def test_pipeline(
        self,
        name: str,
        agc_boost: float,
        python_boost: float,
        arduino_compression: str,
        max_brightness: int = 255
    ) -> Tuple[np.ndarray, Dict]:
        """
        Test a complete amplitude processing pipeline.

        Args:
            name: Pipeline name
            agc_boost: ElevenLabs AGC boost
            python_boost: Python service boost
            arduino_compression: Arduino compression type
            max_brightness: Maximum LED brightness

        Returns:
            (led_brightness_values, statistics)
        """
        print(f"\n{'='*60}")
        print(f"Testing Pipeline: {name}")
        print(f"{'='*60}")
        print(f"  AGC Boost: {agc_boost}x")
        print(f"  Python Boost: {python_boost}x")
        print(f"  Arduino Compression: {arduino_compression}")
        print(f"  Max Brightness: {max_brightness}")

        # Step 1: Calculate RMS timeline
        timestamps, rms_db = self.calculate_rms_timeline()

        # Step 2: Apply AGC normalization
        agc_normalized = self.apply_agc_normalization(rms_db, boost=agc_boost)

        # Step 3: Apply Python boost
        python_boosted = self.apply_python_boost(agc_normalized, boost=python_boost)

        # Step 4: Apply Arduino compression
        arduino_compressed = self.apply_arduino_compression(python_boosted, compression=arduino_compression)

        # Step 5: Calculate LED brightness
        led_brightness = self.calculate_led_brightness(arduino_compressed, max_brightness=max_brightness)

        # Analyze distribution
        stats = self.analyze_distribution(led_brightness, label=name)
        self.print_stats(stats)

        return led_brightness, stats

    def visualize_comparison(
        self,
        timestamps: np.ndarray,
        pipelines: Dict[str, np.ndarray],
        save_path: Path = None
    ):
        """
        Visualize multiple pipeline outputs for comparison.

        Args:
            timestamps: Time values for x-axis
            pipelines: Dict of {pipeline_name: led_brightness_values}
            save_path: Optional path to save figure
        """
        fig, axes = plt.subplots(len(pipelines) + 1, 1, figsize=(14, 3 * (len(pipelines) + 1)))

        # Plot histograms
        ax_hist = axes[0]
        for name, values in pipelines.items():
            ax_hist.hist(values, bins=50, alpha=0.5, label=name, range=(0, 255))
        ax_hist.set_xlabel("LED Brightness (0-255)")
        ax_hist.set_ylabel("Frequency")
        ax_hist.set_title("LED Brightness Distribution Comparison")
        ax_hist.legend()
        ax_hist.grid(True, alpha=0.3)

        # Plot timelines
        for i, (name, values) in enumerate(pipelines.items(), start=1):
            ax = axes[i]
            ax.plot(timestamps[:len(values)], values, linewidth=0.5, alpha=0.8)
            ax.fill_between(timestamps[:len(values)], 0, values, alpha=0.3)
            ax.set_ylabel("LED Brightness")
            ax.set_title(f"{name} - Timeline")
            ax.set_ylim(0, 255)
            ax.grid(True, alpha=0.3)
            ax.axhline(y=255, color='red', linestyle='--', alpha=0.5, label='Max')
            ax.axhline(y=180, color='orange', linestyle='--', alpha=0.5, label='Current Cap')

        axes[-1].set_xlabel("Time (seconds)")

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150)
            print(f"\nVisualization saved to {save_path}")

        plt.show()


def main():
    parser = argparse.ArgumentParser(description="Analyze TTS audio amplitude for LED optimization")
    parser.add_argument("pcm_file", type=Path, help="Path to captured PCM audio file")
    parser.add_argument("--sample-rate", type=int, default=24000, help="Sample rate (default: 24000)")
    parser.add_argument("--save-plot", type=Path, help="Save comparison plot to file")

    args = parser.parse_args()

    if not args.pcm_file.exists():
        print(f"Error: File not found: {args.pcm_file}")
        return

    # Initialize analyzer
    analyzer = AmplitudeAnalyzer(args.pcm_file, sample_rate=args.sample_rate)

    # Get timestamps for plotting
    timestamps, _ = analyzer.calculate_rms_timeline()

    # Test different pipelines
    pipelines = {}

    # Current production setup
    current, _ = analyzer.test_pipeline(
        name="Current (2x AGC, 8x Python, double_sqrt, max=180)",
        agc_boost=2.0,
        python_boost=8.0,
        arduino_compression="double_sqrt",
        max_brightness=180
    )
    pipelines["Current"] = current

    # Recommended: Full brightness range
    recommended1, _ = analyzer.test_pipeline(
        name="Recommended 1 (2x AGC, 4x Python, sqrt, max=255)",
        agc_boost=2.0,
        python_boost=4.0,
        arduino_compression="sqrt",
        max_brightness=255
    )
    pipelines["Recommended 1"] = recommended1

    # Aggressive normalization
    aggressive, _ = analyzer.test_pipeline(
        name="Aggressive (3x AGC, 3x Python, sqrt, max=255)",
        agc_boost=3.0,
        python_boost=3.0,
        arduino_compression="sqrt",
        max_brightness=255
    )
    pipelines["Aggressive"] = aggressive

    # Gentle compression
    gentle, _ = analyzer.test_pipeline(
        name="Gentle (2x AGC, 6x Python, none, max=255)",
        agc_boost=2.0,
        python_boost=6.0,
        arduino_compression="none",
        max_brightness=255
    )
    pipelines["Gentle"] = gentle

    # Visualize comparison
    analyzer.visualize_comparison(timestamps, pipelines, save_path=args.save_plot)


if __name__ == "__main__":
    main()
