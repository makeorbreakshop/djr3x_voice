#!/usr/bin/env python3
"""
Audio Analysis Tool for DJ R-3X Voice Processing

This script analyzes and compares:
1. Original ElevenLabs audio
2. Processed audio samples
3. Real DJ R-3X audio samples

It extracts spectral, temporal, and perceptual features to identify key differences
and suggest improvements to the audio processing chain.
"""

import os
import numpy as np
import matplotlib.pyplot as plt
import librosa
import librosa.display
from pydub import AudioSegment
from pathlib import Path
from typing import Dict, List, Tuple
import random

# Define paths to audio directories
ELEVENLABS_DIR = Path('audio/elevenlabs_audio')
PROCESSED_DIR = Path('audio/processed_audio')
REAL_DIR = Path('audio/real_audio')

# Feature analysis settings
N_FFT = 2048
HOP_LENGTH = 512
N_MELS = 128
N_MFCC = 13

class AudioAnalyzer:
    """Analyzes and compares audio samples for voice processing improvement."""
    
    def __init__(self):
        """Initialize the audio analyzer."""
        self.elevenlabs_samples = []
        self.processed_samples = []
        self.real_samples = []
        self.features = {}
        
    def load_samples(self, max_samples: int = 5):
        """
        Load audio samples from each directory.
        
        Args:
            max_samples: Maximum number of samples to load from each category
        """
        print("Loading audio samples...")
        
        # Load ElevenLabs samples
        if ELEVENLABS_DIR.exists():
            files = list(ELEVENLABS_DIR.glob('*.mp3'))
            for file in files[:max_samples]:
                self.elevenlabs_samples.append(self._load_audio(file))
                
        # Load processed samples
        if PROCESSED_DIR.exists():
            files = list(PROCESSED_DIR.glob('*.mp3'))
            for file in files[:max_samples]:
                self.processed_samples.append(self._load_audio(file))
                
        # Load real samples
        if REAL_DIR.exists():
            files = list(REAL_DIR.glob('*.mp3'))
            # Choose random samples if we have many
            if len(files) > max_samples:
                files = random.sample(files, max_samples)
            for file in files[:max_samples]:
                self.real_samples.append(self._load_audio(file))
                
        print(f"Loaded {len(self.elevenlabs_samples)} ElevenLabs samples")
        print(f"Loaded {len(self.processed_samples)} processed samples")
        print(f"Loaded {len(self.real_samples)} real DJ R-3X samples")
    
    def _load_audio(self, file_path: Path) -> Tuple[np.ndarray, int, str]:
        """
        Load an audio file and convert to mono numpy array.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            Tuple containing audio samples, sample rate, and file name
        """
        # Use librosa for consistent resampling and mono conversion
        print(f"Loading {file_path}")
        y, sr = librosa.load(file_path, sr=44100, mono=True)
        return (y, sr, file_path.name)
    
    def analyze_all_features(self):
        """Extract and analyze features from all audio samples."""
        print("\nAnalyzing audio features...")
        
        # Analyze real samples first (our target)
        real_features = []
        for audio, sr, name in self.real_samples:
            print(f"Analyzing real sample: {name}")
            features = self._extract_features(audio, sr)
            real_features.append(features)
        
        # Average real sample features to create a target profile
        target_features = self._average_features(real_features)
        self.features['real_target'] = target_features
        
        # Analyze elevenlabs and processed samples
        for i, (audio, sr, name) in enumerate(self.elevenlabs_samples):
            print(f"Analyzing ElevenLabs sample: {name}")
            self.features[f'elevenlabs_{i}'] = self._extract_features(audio, sr)
            
        for i, (audio, sr, name) in enumerate(self.processed_samples):
            print(f"Analyzing processed sample: {name}")
            self.features[f'processed_{i}'] = self._extract_features(audio, sr)
    
    def _extract_features(self, audio: np.ndarray, sr: int) -> Dict:
        """
        Extract audio features from a sample.
        
        Args:
            audio: Audio samples as numpy array
            sr: Sample rate
            
        Returns:
            Dictionary of audio features
        """
        features = {}
        
        # Spectral features
        # Mel spectrogram
        mel_spec = librosa.feature.melspectrogram(
            y=audio, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH, n_mels=N_MELS
        )
        features['mel_spec'] = mel_spec
        
        # MFCCs (for timbre analysis)
        mfccs = librosa.feature.mfcc(
            S=librosa.power_to_db(mel_spec), n_mfcc=N_MFCC
        )
        features['mfccs'] = mfccs
        features['mfcc_means'] = np.mean(mfccs, axis=1)
        
        # Spectral centroid (brightness)
        spectral_centroid = librosa.feature.spectral_centroid(
            y=audio, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH
        )
        features['spectral_centroid'] = np.mean(spectral_centroid)
        
        # Spectral contrast (for detecting peaks and valleys in spectrum)
        contrast = librosa.feature.spectral_contrast(
            y=audio, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH
        )
        features['spectral_contrast'] = contrast
        features['contrast_means'] = np.mean(contrast, axis=1)
        
        # Spectral rolloff (frequency below which most energy exists)
        rolloff = librosa.feature.spectral_rolloff(
            y=audio, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH
        )
        features['spectral_rolloff'] = np.mean(rolloff)
        
        # Temporal features
        # RMS energy (for dynamic range analysis)
        rms = librosa.feature.rms(y=audio, frame_length=N_FFT, hop_length=HOP_LENGTH)
        features['rms'] = rms
        features['rms_mean'] = np.mean(rms)
        features['rms_std'] = np.std(rms)
        
        # Zero-crossing rate (for harshness/noisiness)
        zcr = librosa.feature.zero_crossing_rate(audio, frame_length=N_FFT, hop_length=HOP_LENGTH)
        features['zcr'] = zcr
        features['zcr_mean'] = np.mean(zcr)
        
        # Harmonic-percussive source separation
        harmonic, percussive = librosa.effects.hpss(audio)
        features['harmonic_mean'] = np.mean(np.abs(harmonic))
        features['percussive_mean'] = np.mean(np.abs(percussive))
        features['harmonic_ratio'] = np.sum(np.abs(harmonic)) / (np.sum(np.abs(percussive)) + 1e-8)
        
        return features
    
    def _average_features(self, feature_list: List[Dict]) -> Dict:
        """
        Average features across multiple samples.
        
        Args:
            feature_list: List of feature dictionaries
            
        Returns:
            Dictionary of averaged features
        """
        if not feature_list:
            return {}
            
        avg_features = {}
        
        # For each feature, compute the average across all samples
        for key in feature_list[0].keys():
            if key in ['mel_spec', 'mfccs', 'spectral_contrast', 'rms', 'zcr']:
                # For 2D arrays, we need to ensure they have the same shape before averaging
                # Just use the first sample's array shape for these complex features
                avg_features[key] = feature_list[0][key]
            elif isinstance(feature_list[0][key], (np.ndarray, list)):
                # For 1D arrays, make sure they're the same size
                shapes = [f[key].shape for f in feature_list]
                if len(set([len(s) for s in shapes])) == 1:  # All same length
                    avg_features[key] = np.mean([f[key] for f in feature_list], axis=0)
                else:
                    # If arrays have different sizes, just use the first one
                    avg_features[key] = feature_list[0][key]
            elif isinstance(feature_list[0][key], (int, float)):
                # For scalar values
                avg_features[key] = np.mean([f[key] for f in feature_list])
                
        return avg_features
    
    def visualize_comparisons(self):
        """Generate visualizations comparing the different audio types."""
        if not self.features:
            print("No features to visualize. Run analyze_all_features() first.")
            return
            
        print("\nGenerating visualizations...")
        
        # Create output directory
        output_dir = Path('analysis_results')
        output_dir.mkdir(exist_ok=True)
        
        # Plot spectrograms
        self._plot_average_spectrogram(output_dir)
        
        # Plot MFCCs comparison
        self._plot_mfcc_comparison(output_dir)
        
        # Plot spectral features
        self._plot_spectral_features(output_dir)
        
        # Plot dynamic range
        self._plot_dynamic_features(output_dir)
        
        print(f"Visualizations saved to {output_dir}")
    
    def _plot_average_spectrogram(self, output_dir: Path):
        """Plot average spectrograms for each audio type."""
        plt.figure(figsize=(15, 10))
        
        # Get average mel spectrograms
        real_mel = self.features['real_target']['mel_spec']
        
        # Find processed and elevenlabs samples to compare
        processed_key = next((k for k in self.features.keys() if k.startswith('processed_')), None)
        elevenlabs_key = next((k for k in self.features.keys() if k.startswith('elevenlabs_')), None)
        
        if processed_key and elevenlabs_key:
            processed_mel = self.features[processed_key]['mel_spec']
            elevenlabs_mel = self.features[elevenlabs_key]['mel_spec']
            
            # Plot real spectrogram
            plt.subplot(3, 1, 1)
            librosa.display.specshow(
                librosa.power_to_db(real_mel, ref=np.max),
                y_axis='mel', x_axis='time', sr=44100, hop_length=HOP_LENGTH
            )
            plt.title('Real DJ R-3X Mel Spectrogram')
            plt.colorbar(format='%+2.0f dB')
            
            # Plot processed spectrogram
            plt.subplot(3, 1, 2)
            librosa.display.specshow(
                librosa.power_to_db(processed_mel, ref=np.max),
                y_axis='mel', x_axis='time', sr=44100, hop_length=HOP_LENGTH
            )
            plt.title('Processed Audio Mel Spectrogram')
            plt.colorbar(format='%+2.0f dB')
            
            # Plot elevenlabs spectrogram
            plt.subplot(3, 1, 3)
            librosa.display.specshow(
                librosa.power_to_db(elevenlabs_mel, ref=np.max),
                y_axis='mel', x_axis='time', sr=44100, hop_length=HOP_LENGTH
            )
            plt.title('ElevenLabs Original Mel Spectrogram')
            plt.colorbar(format='%+2.0f dB')
            
            plt.tight_layout()
            plt.savefig(output_dir / 'mel_spectrograms_comparison.png')
            plt.close()
    
    def _plot_mfcc_comparison(self, output_dir: Path):
        """Plot MFCC comparison between real, processed, and original audio."""
        plt.figure(figsize=(10, 8))
        
        # Plot MFCC means for all sample types
        real_mfcc_means = self.features['real_target']['mfcc_means']
        
        # Collect means from processed and elevenlabs samples
        processed_means = []
        elevenlabs_means = []
        
        for key, features in self.features.items():
            if key.startswith('processed_'):
                processed_means.append(features['mfcc_means'])
            elif key.startswith('elevenlabs_'):
                elevenlabs_means.append(features['mfcc_means'])
        
        # Average if we have multiple samples
        if processed_means:
            processed_mfcc_means = np.mean(processed_means, axis=0)
            plt.plot(processed_mfcc_means, 'g-', label='Processed Audio', linewidth=2)
            
        if elevenlabs_means:
            elevenlabs_mfcc_means = np.mean(elevenlabs_means, axis=0)
            plt.plot(elevenlabs_mfcc_means, 'b-', label='ElevenLabs Original', linewidth=2)
        
        plt.plot(real_mfcc_means, 'r-', label='Real DJ R-3X', linewidth=2)
        
        plt.title('MFCC Comparison (Timbre Analysis)')
        plt.xlabel('MFCC Coefficient')
        plt.ylabel('Magnitude')
        plt.legend()
        plt.grid(True)
        plt.savefig(output_dir / 'mfcc_comparison.png')
        plt.close()
    
    def _plot_spectral_features(self, output_dir: Path):
        """Plot spectral feature comparison."""
        plt.figure(figsize=(12, 10))
        
        # Safely get feature values
        def safe_get(features_dict, key, default=0):
            return features_dict.get(key, default)
        
        # Get feature values
        real = self.features['real_target']
        real_centroid = safe_get(real, 'spectral_centroid')
        real_rolloff = safe_get(real, 'spectral_rolloff')
        real_contrast = safe_get(real, 'contrast_means', np.array([0]))
        
        # Collect values from processed and elevenlabs samples
        processed_centroids = []
        processed_rolloffs = []
        processed_contrasts = []
        
        elevenlabs_centroids = []
        elevenlabs_rolloffs = []
        elevenlabs_contrasts = []
        
        for key, features in self.features.items():
            if key.startswith('processed_'):
                processed_centroids.append(safe_get(features, 'spectral_centroid'))
                processed_rolloffs.append(safe_get(features, 'spectral_rolloff'))
                contrast = safe_get(features, 'contrast_means', np.array([0]))
                processed_contrasts.append(contrast)
            elif key.startswith('elevenlabs_'):
                elevenlabs_centroids.append(safe_get(features, 'spectral_centroid'))
                elevenlabs_rolloffs.append(safe_get(features, 'spectral_rolloff'))
                contrast = safe_get(features, 'contrast_means', np.array([0]))
                elevenlabs_contrasts.append(contrast)
        
        # Plot spectral centroid comparison
        plt.subplot(3, 1, 1)
        if processed_centroids:
            proc_centroid_mean = np.mean(processed_centroids)
            plt.bar(1, proc_centroid_mean, width=0.4, color='g', label='Processed')
        
        if elevenlabs_centroids:
            el_centroid_mean = np.mean(elevenlabs_centroids)
            plt.bar(2, el_centroid_mean, width=0.4, color='b', label='ElevenLabs')
            
        plt.bar(3, real_centroid, width=0.4, color='r', label='Real DJ R-3X')
        plt.title('Spectral Centroid (Brightness)')
        plt.xticks([1, 2, 3], ['Processed', 'ElevenLabs', 'Real DJ R-3X'])
        plt.legend()
        
        # Plot spectral rolloff comparison
        plt.subplot(3, 1, 2)
        if processed_rolloffs:
            proc_rolloff_mean = np.mean(processed_rolloffs)
            plt.bar(1, proc_rolloff_mean, width=0.4, color='g', label='Processed')
        
        if elevenlabs_rolloffs:
            el_rolloff_mean = np.mean(elevenlabs_rolloffs)
            plt.bar(2, el_rolloff_mean, width=0.4, color='b', label='ElevenLabs')
            
        plt.bar(3, real_rolloff, width=0.4, color='r', label='Real DJ R-3X')
        plt.title('Spectral Rolloff (Frequency Cutoff)')
        plt.xticks([1, 2, 3], ['Processed', 'ElevenLabs', 'Real DJ R-3X'])
        plt.legend()
        
        # Plot spectral contrast comparison - only if we have data
        plt.subplot(3, 1, 3)
        if len(real_contrast) > 0:
            contrast_bands = np.arange(len(real_contrast))
            
            if processed_contrasts and len(processed_contrasts[0]) > 0:
                # Make sure all arrays are the same length
                min_length = min(len(real_contrast), min(len(c) for c in processed_contrasts if len(c) > 0))
                proc_contrast_mean = np.mean([c[:min_length] for c in processed_contrasts if len(c) > 0], axis=0) if any(len(c) > 0 for c in processed_contrasts) else np.array([0])
                plt.plot(np.arange(len(proc_contrast_mean)), proc_contrast_mean, 'g-', label='Processed', linewidth=2)
            
            if elevenlabs_contrasts and len(elevenlabs_contrasts[0]) > 0:
                # Make sure all arrays are the same length
                min_length = min(len(real_contrast), min(len(c) for c in elevenlabs_contrasts if len(c) > 0))
                el_contrast_mean = np.mean([c[:min_length] for c in elevenlabs_contrasts if len(c) > 0], axis=0) if any(len(c) > 0 for c in elevenlabs_contrasts) else np.array([0])
                plt.plot(np.arange(len(el_contrast_mean)), el_contrast_mean, 'b-', label='ElevenLabs', linewidth=2)
                
            plt.plot(contrast_bands, real_contrast, 'r-', label='Real DJ R-3X', linewidth=2)
            plt.title('Spectral Contrast (Peak/Valley Differences)')
            plt.xlabel('Frequency Band')
            plt.ylabel('Contrast (dB)')
            plt.legend()
        else:
            plt.text(0.5, 0.5, 'Spectral contrast data not available', 
                     horizontalalignment='center', verticalalignment='center',
                     transform=plt.gca().transAxes)
            plt.title('Spectral Contrast (Peak/Valley Differences)')
        
        plt.tight_layout()
        plt.savefig(output_dir / 'spectral_features_comparison.png')
        plt.close()
    
    def _plot_dynamic_features(self, output_dir: Path):
        """Plot dynamic feature comparison."""
        plt.figure(figsize=(12, 10))
        
        # Safely get feature values with fallbacks to prevent KeyErrors
        def safe_get(features_dict, key, default=0):
            return features_dict.get(key, default)
        
        # Get feature values
        real = self.features['real_target']
        real_rms_mean = safe_get(real, 'rms_mean')
        real_rms_std = safe_get(real, 'rms_std')
        real_zcr = safe_get(real, 'zcr_mean')
        real_harmonic_ratio = safe_get(real, 'harmonic_ratio')
        
        # Collect values from processed and elevenlabs samples
        processed_rms_means = []
        processed_rms_stds = []
        processed_zcrs = []
        processed_harm_ratios = []
        
        elevenlabs_rms_means = []
        elevenlabs_rms_stds = []
        elevenlabs_zcrs = []
        elevenlabs_harm_ratios = []
        
        for key, features in self.features.items():
            if key.startswith('processed_'):
                processed_rms_means.append(safe_get(features, 'rms_mean'))
                processed_rms_stds.append(safe_get(features, 'rms_std'))
                processed_zcrs.append(safe_get(features, 'zcr_mean'))
                processed_harm_ratios.append(safe_get(features, 'harmonic_ratio'))
            elif key.startswith('elevenlabs_'):
                elevenlabs_rms_means.append(safe_get(features, 'rms_mean'))
                elevenlabs_rms_stds.append(safe_get(features, 'rms_std'))
                elevenlabs_zcrs.append(safe_get(features, 'zcr_mean'))
                elevenlabs_harm_ratios.append(safe_get(features, 'harmonic_ratio'))
        
        # Plot RMS energy comparison
        plt.subplot(2, 2, 1)
        if processed_rms_means:
            proc_rms_mean = np.mean(processed_rms_means)
            plt.bar(1, proc_rms_mean, width=0.4, color='g', label='Processed')
        
        if elevenlabs_rms_means:
            el_rms_mean = np.mean(elevenlabs_rms_means)
            plt.bar(2, el_rms_mean, width=0.4, color='b', label='ElevenLabs')
            
        plt.bar(3, real_rms_mean, width=0.4, color='r', label='Real DJ R-3X')
        plt.title('RMS Energy (Loudness)')
        plt.xticks([1, 2, 3], ['Processed', 'ElevenLabs', 'Real DJ R-3X'])
        plt.legend()
        
        # Plot RMS standard deviation comparison
        plt.subplot(2, 2, 2)
        if processed_rms_stds:
            proc_rms_std = np.mean(processed_rms_stds)
            plt.bar(1, proc_rms_std, width=0.4, color='g', label='Processed')
        
        if elevenlabs_rms_stds:
            el_rms_std = np.mean(elevenlabs_rms_stds)
            plt.bar(2, el_rms_std, width=0.4, color='b', label='ElevenLabs')
            
        plt.bar(3, real_rms_std, width=0.4, color='r', label='Real DJ R-3X')
        plt.title('RMS Energy STD (Dynamic Range)')
        plt.xticks([1, 2, 3], ['Processed', 'ElevenLabs', 'Real DJ R-3X'])
        plt.legend()
        
        # Plot Zero-crossing rate comparison
        plt.subplot(2, 2, 3)
        if processed_zcrs:
            proc_zcr = np.mean(processed_zcrs)
            plt.bar(1, proc_zcr, width=0.4, color='g', label='Processed')
        
        if elevenlabs_zcrs:
            el_zcr = np.mean(elevenlabs_zcrs)
            plt.bar(2, el_zcr, width=0.4, color='b', label='ElevenLabs')
            
        plt.bar(3, real_zcr, width=0.4, color='r', label='Real DJ R-3X')
        plt.title('Zero-Crossing Rate (Noisiness/Harshness)')
        plt.xticks([1, 2, 3], ['Processed', 'ElevenLabs', 'Real DJ R-3X'])
        plt.legend()
        
        # Plot Harmonic ratio comparison
        plt.subplot(2, 2, 4)
        if processed_harm_ratios:
            proc_harm_ratio = np.mean(processed_harm_ratios)
            plt.bar(1, proc_harm_ratio, width=0.4, color='g', label='Processed')
        
        if elevenlabs_harm_ratios:
            el_harm_ratio = np.mean(elevenlabs_harm_ratios)
            plt.bar(2, el_harm_ratio, width=0.4, color='b', label='ElevenLabs')
            
        plt.bar(3, real_harmonic_ratio, width=0.4, color='r', label='Real DJ R-3X')
        plt.title('Harmonic/Percussive Ratio (Tone vs. Noise)')
        plt.xticks([1, 2, 3], ['Processed', 'ElevenLabs', 'Real DJ R-3X'])
        plt.legend()
        
        plt.tight_layout()
        plt.savefig(output_dir / 'dynamic_features_comparison.png')
        plt.close()
    
    def generate_recommendations(self) -> str:
        """
        Generate recommendations for improving the audio processing chain.
        
        Returns:
            A string containing recommendations
        """
        if not self.features or 'real_target' not in self.features:
            return "No analysis data available. Run analyze_all_features() first."
        
        recommendations = []
        
        # Find the first processed and elevenlabs samples
        processed_key = next((k for k in self.features.keys() if k.startswith('processed_')), None)
        
        if not processed_key:
            return "No processed samples available for comparison."
        
        # Safely get feature values
        def safe_get(features_dict, key, default=0):
            return features_dict.get(key, default)
        
        processed = self.features[processed_key]
        real = self.features['real_target']
        
        # Compare spectral centroids (brightness)
        real_centroid = safe_get(real, 'spectral_centroid')
        proc_centroid = safe_get(processed, 'spectral_centroid')
        
        if real_centroid > 0:  # Avoid division by zero
            centroid_diff = proc_centroid - real_centroid
            if abs(centroid_diff) > real_centroid * 0.15:  # If >15% different
                if centroid_diff > 0:
                    recommendations.append(
                        "SPECTRAL: Processed audio is too bright. Consider reducing high frequencies "
                        "or adjusting the highpass filter cutoff to a lower frequency."
                    )
                else:
                    recommendations.append(
                        "SPECTRAL: Processed audio is too dark. Consider boosting high frequencies "
                        "or increasing the presence boost gain."
                    )
        
        # Compare harmonic ratios
        real_harmonic_ratio = safe_get(real, 'harmonic_ratio')
        proc_harmonic_ratio = safe_get(processed, 'harmonic_ratio')
        
        if real_harmonic_ratio > 0:  # Avoid division by zero
            harm_diff = proc_harmonic_ratio - real_harmonic_ratio
            if abs(harm_diff) > real_harmonic_ratio * 0.2:  # If >20% different
                if harm_diff > 0:
                    recommendations.append(
                        "DISTORTION: Processed audio is too clean. Consider increasing distortion "
                        "or adding more noise components (e.g., bit crushing)."
                    )
                else:
                    recommendations.append(
                        "DISTORTION: Processed audio is too distorted. Consider reducing distortion drive "
                        "or adjusting the distortion curve."
                    )
        
        # Compare RMS (dynamics)
        real_rms_mean = safe_get(real, 'rms_mean')
        proc_rms_mean = safe_get(processed, 'rms_mean')
        real_rms_std = safe_get(real, 'rms_std')
        proc_rms_std = safe_get(processed, 'rms_std')
        
        if real_rms_mean > 0:  # Avoid division by zero
            rms_mean_diff = proc_rms_mean - real_rms_mean
            if abs(rms_mean_diff) > real_rms_mean * 0.2:
                if rms_mean_diff > 0:
                    recommendations.append(
                        "DYNAMICS: Processed audio is too loud. Consider reducing overall gain "
                        "or adjusting compressor threshold."
                    )
                else:
                    recommendations.append(
                        "DYNAMICS: Processed audio is too quiet. Consider increasing overall gain "
                        "or adjusting compressor threshold."
                    )
        
        if real_rms_std > 0:  # Avoid division by zero
            rms_std_diff = proc_rms_std - real_rms_std
            if abs(rms_std_diff) > real_rms_std * 0.2:
                if rms_std_diff > 0:
                    recommendations.append(
                        "DYNAMICS: Processed audio has too much dynamic range. Consider more aggressive "
                        "compression (higher ratio or lower threshold)."
                    )
                else:
                    recommendations.append(
                        "DYNAMICS: Processed audio is too compressed. Consider less aggressive "
                        "compression (lower ratio or higher threshold)."
                    )
        
        # Compare MFCCs (timbre)
        if 'mfcc_means' in real and 'mfcc_means' in processed:
            if len(real['mfcc_means']) > 0 and len(processed['mfcc_means']) > 0:
                # Make arrays the same length for comparison
                min_len = min(len(real['mfcc_means']), len(processed['mfcc_means']))
                mfcc_diff = np.mean(np.abs(processed['mfcc_means'][:min_len] - real['mfcc_means'][:min_len]))
                if mfcc_diff > 0.5:  # Threshold based on typical MFCC ranges
                    recommendations.append(
                        "TIMBRE: Significant timbre differences detected. Consider adding "
                        "robot-like effects such as ring modulation, comb filtering with longer "
                        "delay times (5-10ms), or formant shifting."
                    )
        
        # Check for missing effects
        real_zcr = safe_get(real, 'zcr_mean')
        proc_zcr = safe_get(processed, 'zcr_mean')
        
        if real_zcr > 0:  # Avoid division by zero
            zcr_diff = proc_zcr - real_zcr
            if abs(zcr_diff) > real_zcr * 0.3:
                if zcr_diff < 0:
                    recommendations.append(
                        "EFFECTS: Missing high-frequency artifacts typical of robot voices. "
                        "Consider adding bit crusher, sample rate reduction, or metallic resonance."
                    )
        
        # Add general recommendations if we detected significant differences
        if len(recommendations) > 0:
            recommendations.append(
                "\nGENERAL: Current comb filter delay (0.5ms) is likely too short for "
                "a robot effect. Try increasing to 5-10ms and adjusting feedback."
            )
            recommendations.append(
                "GENERAL: Consider adding ring modulation (50-100Hz carrier frequency) "
                "for a more synthetic character."
            )
            recommendations.append(
                "GENERAL: Try experimenting with effect ordering - place modulation effects "
                "before/after distortion to find the optimal chain."
            )
        else:
            recommendations.append("The processing chain is already closely matching the real DJ R-3X sound!")
        
        return "\n\n".join(recommendations)

def main():
    """Main function to run the audio analysis."""
    analyzer = AudioAnalyzer()
    analyzer.load_samples()
    analyzer.analyze_all_features()
    analyzer.visualize_comparisons()
    
    recommendations = analyzer.generate_recommendations()
    
    # Save recommendations to file
    output_dir = Path('analysis_results')
    output_dir.mkdir(exist_ok=True)
    
    with open(output_dir / 'recommendations.txt', 'w') as f:
        f.write(recommendations)
    
    print("\nAnalysis completed!")
    print("\nRecommendations for improving the audio processing chain:")
    print("=" * 80)
    print(recommendations)
    print("=" * 80)
    print(f"\nDetailed analysis results saved to {output_dir}")

if __name__ == "__main__":
    main() 