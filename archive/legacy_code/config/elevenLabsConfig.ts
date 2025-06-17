// Eleven Labs Voice Configuration

export interface ElevenLabsVoiceConfig {
  // Voice Settings
  stability: number;       // Range: 0-100, Default: 50
  similarity: number;      // Range: 0-100, Default: 75
  styleExaggeration: number; // Range: 0-100, Default: 0
  speakerBoost: boolean;  // Default: false
  
  // Model Selection
  model: 'eleven_multilingual_v2' | 'eleven_flash_v2.5';
  
  // Model Specific Settings
  characterLimit: number;  // 10,000 for multilingual_v2, 40,000 for flash_v2.5
}

// Default configuration
export const defaultVoiceConfig: ElevenLabsVoiceConfig = {
  stability: 50,          // Balanced setting for emotional range
  similarity: 75,         // Recommended similarity to original voice
  styleExaggeration: 0,   // Recommended to keep at 0 for stability
  speakerBoost: false,    // Disabled by default to minimize latency
  model: 'eleven_multilingual_v2', // Using the most lifelike model by default
  characterLimit: 10000   // Default for eleven_multilingual_v2
};

// Helper function to validate and merge custom settings
export function createVoiceConfig(customConfig: Partial<ElevenLabsVoiceConfig>): ElevenLabsVoiceConfig {
  const config = {
    ...defaultVoiceConfig,
    ...customConfig
  };

  // Validate ranges
  config.stability = Math.max(0, Math.min(100, config.stability));
  config.similarity = Math.max(0, Math.min(100, config.similarity));
  config.styleExaggeration = Math.max(0, Math.min(100, config.styleExaggeration));
  
  // Update character limit based on model
  config.characterLimit = config.model === 'eleven_flash_v2.5' ? 40000 : 10000;

  return config;
}

// Usage example:
/*
const customVoiceConfig = createVoiceConfig({
  stability: 45,
  similarity: 85,
  model: 'eleven_flash_v2.5'
});
*/ 