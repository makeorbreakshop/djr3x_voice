# DJ R3X Voice App — Working Dev Log (2025-01-15)
- This gets refreshed daily and the core info is saved to `dj-r3x-condensed-dev-log.md`
- Goal is to give cursor good active context for what we are working on.

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project recreates the voice and animation features with interactive conversations and synchronized LED animations.

## [Current Session] Issue #46: System Migration and Dependency Issues

**Issue**: DJ R3X system was working on another computer but experiencing multiple failures on new macOS environment.

**Initial Error Symptoms**:
```
Error: No module named 'mpv' - ElevenLabs streaming audio failed
VLC media duration detection warnings
TimelineExecutorService AttributeError: 'TimelineExecutorService' object has no attribute 'name'
Import errors when trying to run main.py
```

**Root Cause Analysis**: Environment setup differences between working system and new macOS installation, not actual code bugs.

**Actions Taken**:
1. **Installed missing system dependencies**:
   - `brew install mpv` - Required for ElevenLabs streaming audio
   - Verified VLC already installed via Homebrew

2. **Updated Python packages**:
   - `pip install --upgrade elevenlabs deepgram-sdk openai python-vlc`
   - ElevenLabs upgraded from older version to 2.3.0

3. **Avoided premature code changes**:
   - Initially attempted to fix perceived code issues
   - User correctly stopped changes - code was working before
   - Focus shifted to environmental differences

**Current Status**: Dependencies installed, but still investigating proper module execution method.

**Learning**: When system "was working before", investigate environment differences first rather than assuming code bugs. System dependencies like mpv and proper Python package versions are critical for audio services.

**Next Steps**: 
- Determine correct way to run the application (module import vs direct script execution)
- Verify all system-level audio dependencies are properly configured
- Test ElevenLabs streaming with new mpv installation

## Issue #47: ElevenLabs SDK Version Mismatch and VLC Media Errors

**Issue**: System starts successfully but ElevenLabs text-to-speech fails due to API version mismatch, and VLC reports media creation failures.

**Error Symptoms**:
```
Error in audio thread streaming: 'RealtimeTextToSpeechClient' object has no attribute 'convert_as_stream'
Could not get duration for /path/to/music.mp3: 'NoneType' object has no attribute 'media_new'
```

**Root Cause Analysis**: 
1. **ElevenLabs SDK Issue**: Code was using old API method `convert_as_stream()` but newer SDK v2.3.0 uses `stream()` method
2. **VLC Issue**: VLC instance creation on macOS sometimes fails, leading to `None` instance

**Actions Taken**:
1. **Fixed ElevenLabs Streaming API**:
   - Updated `cantina_os/services/elevenlabs_service.py` line 360 to use `stream()` instead of `convert_as_stream()`
   - Updated requirements.txt to specify `elevenlabs>=2.3.0` instead of `>=0.2.26`

2. **Improved VLC Error Handling**:
   - Added null checks for VLC instance and media creation in `music_controller_service.py`
   - Made duration detection more robust with proper error handling

**Status**: Fixes implemented - ready for testing

**Learning**: SDK upgrades can introduce breaking API changes. Always check documentation when updating dependencies, and ensure version constraints in requirements files match expected API versions.

## Issue #48: VLC Instance Creation Failing After Environment Setup

**Issue**: Music playback completely broken - VLC instance creation returns `None` during service initialization, causing "VLC instance not available" errors when trying to play music.

**Error Symptoms**:
```
All VLC instance creation attempts failed. Music playback will be disabled.
VLC instance not available. Cannot play music.
```

**Environment Context**: 
- VLC installed via Homebrew (`brew install vlc`) 
- `python-vlc==3.0.21203` installed in venv
- `pydub==0.25.1` was missing, now installed
- Manual VLC testing works: `vlc.Instance()` creates valid instances in isolation

**Actions Taken**:
1. **Confirmed VLC Installation**: Verified VLC accessible via `which vlc` and `python -c "import vlc"`
2. **Installed Missing Dependencies**: Added `pydub==0.25.1` to virtual environment  
3. **Attempted Code Fixes**: Modified VLC instance creation with fallback options and error handling ❌
4. **Environment Testing**: Confirmed VLC instance creation works outside application context

**Current Status**: VLC works in isolation but fails during application startup. Issue likely environmental/timing related, not code-related since this was working before.

**Root Cause Found**: VLC was installed as a macOS app bundle (`/Applications/VLC.app`) via Homebrew Cask, but `python-vlc` couldn't find the libraries inside the app bundle.

**Solution Applied**: 
1. **Environment Variables**: Added proper VLC paths to both startup scripts (`launch-dj-r3x.sh` and `/usr/local/bin/dj-r3x`):
   - `VLC_PLUGIN_PATH="/Applications/VLC.app/Contents/MacOS/plugins"`
   - `DYLD_LIBRARY_PATH="/Applications/VLC.app/Contents/MacOS/lib:$DYLD_LIBRARY_PATH"`

2. **Verification**: Confirmed VLC instance creation works with proper environment variables:
   ```bash
   python -c "import vlc; instance = vlc.Instance(); print('Success!')"
   ```

**Final Root Cause**: The code in `music_controller_service.py` was explicitly overriding the `VLC_PLUGIN_PATH` environment variable:
```python
os.environ['VLC_PLUGIN_PATH'] = ''  # Don't scan for additional plugins
```

**Complete Solution**: 
1. **Environment Variables**: Added proper VLC paths to both startup scripts (`launch-dj-r3x.sh` and `/usr/local/bin/dj-r3x`):
   - `VLC_PLUGIN_PATH="/Applications/VLC.app/Contents/MacOS/plugins"`
   - `DYLD_LIBRARY_PATH="/Applications/VLC.app/Contents/MacOS/lib:$DYLD_LIBRARY_PATH"`

2. **Code Fix**: Removed the line in `music_controller_service.py` that was overriding `VLC_PLUGIN_PATH`:
   ```python
   # REMOVED: os.environ['VLC_PLUGIN_PATH'] = ''
   # ADDED: # Note: VLC_PLUGIN_PATH is set in startup scripts to point to VLC.app bundle
   ```

**Status**: ✅ **RESOLVED** - VLC instance creation now works and music playback is functional.

**Learning**: When troubleshooting environment issues, check for code that might be overriding environment variables. The fix required both setting the environment variables AND removing the code that was clearing them.
