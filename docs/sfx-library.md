# DJ R3X Sound Effects Library

This document contains the planned sound effects for DJ R3X's interactive experience, organized by category. Each effect includes the exact prompt to use with ElevenLabs' SFX API and recommended parameters.

## 🎵 Musical Elements

### Transitions & Stingers
- **DJ Scratch Transition**: "Vinyl record scratch effect with futuristic electronic twist, 140 BPM"
  - Duration: 1.0s
  - Prompt Influence: 0.8
- **Bass Drop**: "Deep electronic bass drop with sci-fi resonance"
  - Duration: 1.5s
  - Prompt Influence: 0.7
- **Cantina Stinger**: "Upbeat brass stab in Bb with electronic filter sweep"
  - Duration: 0.8s
  - Prompt Influence: 0.9

## 🤖 Droid Sounds

### Reactions
- **Happy Beep**: "Cheerful droid beep sequence, ascending pitch"
  - Duration: 0.5s
  - Prompt Influence: 0.9
- **Confused Warble**: "Electronic warbling sound with questioning tone"
  - Duration: 0.8s
  - Prompt Influence: 0.8
- **Excited Chirp**: "Quick series of high-pitched electronic chirps"
  - Duration: 0.6s
  - Prompt Influence: 0.9

### Movement
- **Servo Movement**: "Smooth robotic servo motor with slight whine"
  - Duration: 0.7s
  - Prompt Influence: 0.9
- **Mechanical Turn**: "Mechanical rotation sound with electronic undertones"
  - Duration: 1.0s
  - Prompt Influence: 0.8
- **Droid Roll**: "Rolling droid movement with subtle electronic hum"
  - Duration: 1.2s
  - Prompt Influence: 0.7

## 🏢 Cantina Ambience

### Background
- **Crowd Murmur**: "Alien cantina crowd ambience with various creature sounds"
  - Duration: 8.0s
  - Prompt Influence: 0.6
- **Bar Service**: "Distant sounds of glasses clinking and service droids beeping"
  - Duration: 5.0s
  - Prompt Influence: 0.7
- **Door Whoosh**: "Futuristic sliding door with pneumatic hiss"
  - Duration: 1.0s
  - Prompt Influence: 0.9

### Effects
- **Glass Clink**: "Multiple glasses clinking with metallic resonance"
  - Duration: 0.5s
  - Prompt Influence: 0.9
- **Drink Pour**: "Liquid pouring into metallic container with fizz"
  - Duration: 1.2s
  - Prompt Influence: 0.8
- **Hologram Activation**: "Holographic display activation with electronic hum"
  - Duration: 0.8s
  - Prompt Influence: 0.9

## ⚡ Tech & Interface

### UI Sounds
- **Button Press**: "Sci-fi interface button press with satisfying click"
  - Duration: 0.3s
  - Prompt Influence: 0.9
- **Menu Select**: "Futuristic UI selection sound with slight reverb"
  - Duration: 0.4s
  - Prompt Influence: 0.9
- **Alert Chime**: "Gentle electronic alert chime with harmonic overtones"
  - Duration: 0.6s
  - Prompt Influence: 0.8

### System Sounds
- **Power Up**: "Electronic device powering up with rising pitch"
  - Duration: 1.5s
  - Prompt Influence: 0.8
- **Data Transfer**: "Digital data transfer sound with electronic chirps"
  - Duration: 0.8s
  - Prompt Influence: 0.7
- **System Ready**: "Positive electronic confirmation tone sequence"
  - Duration: 0.5s
  - Prompt Influence: 0.9

## Usage Guidelines

1. **Prompt Influence Settings**:
   - Higher values (0.8-0.9) for mechanical/electronic sounds
   - Medium values (0.6-0.7) for ambient/atmospheric sounds
   - Lower values (0.4-0.5) for musical elements that need variation

2. **Duration Guidelines**:
   - Short effects (0.3-0.8s) for UI and reaction sounds
   - Medium effects (1.0-2.0s) for movement and transitions
   - Long effects (4.0-8.0s) for ambient and musical loops

3. **Mixing Considerations**:
   - Ambient sounds should be mixed at lower volumes
   - UI/System sounds should be clear but not dominant
   - Musical elements should duck under speech
   - Droid sounds should be prominent during interactions

4. **Implementation Notes**:
   - Cache all effects after generation
   - Use consistent file naming: `category_name_duration.mp3`
   - Store metadata (duration, category, description) in JSON
   - Implement volume control per category 