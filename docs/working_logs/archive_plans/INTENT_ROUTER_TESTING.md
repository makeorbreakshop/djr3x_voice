# 🎤 IntentRouter Testing Guide

This guide helps you test and debug the IntentRouter feature, which enables DJ R3X to process voice commands and take actions based on them.

## Understanding the Issue

The IntentRouter processes GPT function calls to trigger actions. The recent investigation revealed:

1. The system correctly registers tools
2. The streaming response processing needed enhancement to properly detect and accumulate tool calls
3. Input phrases without explicit commands don't trigger function calls

## Test Commands

Use these clear, explicit commands when testing to ensure the IntentRouter properly detects your intent:

### Music Commands
```
"DJ R3X, play some music."
"Play the next song."
"Stop the music."
"Set the volume to 75 percent."
```

### LED/Eye Commands
```
"Change your eye color to blue."
"Make your eyes red."
"Blink your eyes."
"Show a rainbow pattern."
```

### Conversation Commands
```
"Switch to normal mode."
"Switch to Yoda mode."
"Tell me a joke."
"What time is it?"
```

## Debugging Steps

If commands aren't working:

1. Check logs for tool call processing:
   ```
   Found X complete tool calls to process
   Tool call 1: function=set_mode, args={"mode": "normal"}
   ```

2. Look for INTENT_DETECTED events:
   ```
   Emitting intent: set_mode with params: {"mode": "normal"}
   ```

3. Verify the streaming tool call processing:
   ```
   Processing tool call chunk X in stream
   ```

4. Be explicit in your commands - don't say "can you" or "would you" as these make it less clear you're giving a command.

## How to Verify Success

When working correctly, you should see:
1. Text response from DJ R3X acknowledging the command
2. The corresponding hardware action (LEDs change, music plays, etc.)
3. Log entries showing the command processing flow

If you still encounter issues, try restarting the system and using the most explicit commands possible.

## Log Verbosity

To see detailed logs about intent processing:

```
debug level info
```

For even more verbose logging:

```
debug level debug
```

This will show you the full tool call processing steps and help diagnose any issues.

## 🧪 Testing the Feature

The IntentRouter works by detecting specific action-oriented commands in your speech and routing them to the appropriate hardware functions. To properly test this functionality, you need to use voice commands that explicitly request actions.

### 📋 Test Prerequisites

1. Start DJ R3X in interactive mode:
   ```
   engage
   ```

2. Ensure the logs are set to INFO level:
   ```
   debug level info
   ```

3. Make sure your microphone is working properly

### 🔊 Example Voice Commands

Use these specific voice commands to test different aspects of the IntentRouter:

#### 🎵 Music Control Commands

| Voice Command | Expected Result |
|---------------|-----------------|
| "Play the Cantina song" | 1. Verbal acknowledgment<br>2. Music playback starts |
| "DJ Rex, can you play some upbeat music?" | 1. Verbal response<br>2. Music playback starts |
| "Stop the music" | 1. Verbal acknowledgment<br>2. Music playback stops |
| "I'd like you to stop playing music now" | 1. Verbal response<br>2. Music playback stops |

#### 👁️ Eye Control Commands

| Voice Command | Expected Result |
|---------------|-----------------|
| "Change your eyes to blue" | 1. Verbal acknowledgment<br>2. Eye LEDs change to blue |
| "Make your eyes red and scary" | 1. Verbal response<br>2. Eye LEDs change to red |
| "Can you set your eye color to green?" | 1. Verbal response<br>2. Eye LEDs change to green |
| "Switch your eye pattern to pulsing and make them purple" | 1. Verbal response<br>2. Eye LEDs change to purple with pulsing pattern |

### 📊 Verifying Success

To confirm the IntentRouter is working properly:

1. **Check the Logs**:
   - Look for the following log entries:
     ```
     Processing tool call: function=play_music
     Emitting intent: play_music with params: {"track": "cantina song"}
     Successfully emitted play_music intent
     ```

2. **Observe Hardware Responses**:
   - Music should start/stop when requested
   - Eye LEDs should change color/pattern as requested

3. **Verbal Response**:
   - DJ R3X should respond conversationally without including technical details
   - The response should acknowledge your request in a natural way

## 🧩 Debugging Common Issues

If the IntentRouter is not working as expected:

1. **No Tool Calls Detected**: 
   - Your command might be too ambiguous or not action-oriented
   - Try using more direct language like "Play X" or "Change your eyes to Y"
   
2. **Intent Detected but No Action**:
   - Check logs for successful intent emission
   - Verify that the hardware services are running and responding
   
3. **Parameter Validation Errors**:
   - The command may have unrecognized parameters
   - Check logs for validation errors and adjust your commands

## 🚀 Model Performance Notes

Different OpenAI models may have varying performance with function calling:

- **gpt-4o**: Most reliable for detecting intents in natural language
- **gpt-4.1-mini**: Works well with direct commands but may miss intents in very conversational requests
- **gpt-3.5-turbo**: Limited function calling capabilities

For best results with gpt-4.1-mini (current default), use direct command phrasing rather than highly conversational requests.

## 📝 Reporting Issues

If you encounter persistent issues, please add them to the bug tracker with:

1. The exact voice command used
2. Relevant log snippets
3. Expected vs. actual behavior

---

Happy testing! 🤖🎵 