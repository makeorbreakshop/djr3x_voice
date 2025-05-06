# DJ R3X Music Directory

This directory contains music files that DJ R3X can play. The system supports various audio formats including MP3, WAV, M4A, OGG, and FLAC.

## Adding Music

To add music to DJ R3X:
1. Simply place audio files in this directory
2. Files will be automatically discovered when using music commands
3. No need to restart the application - new files are detected when listing music

## Music Playback Commands

The following commands are available from the R3X Command prompt:

| Command | Description |
|---------|-------------|
| `list music` | Shows a numbered list of all available music tracks |
| `play music <number>` | Plays the track with the given number from the list |
| `play music <name>` | Plays a track that matches the given name (partial matches work) |
| `stop music` | Stops any currently playing music |

## Shortcuts

| Shortcut | Full Command |
|----------|-------------|
| `l music` | `list music` |
| `p music <number/name>` | `play music <number/name>` |

## Examples

```
R3X Command> list music
Available Music Tracks:
1: Cantina Song aka Mad About Mad About Me (From "Star Wars- Galaxy's Edge Oga's Cantina"-....mp3
2: Doolstan.mp3
...

R3X Command> play music 1
Now playing: Cantina Song aka Mad About Mad About Me (From "Star Wars- Galaxy's Edge Oga's Cantina"-....mp3

R3X Command> play music Cantina
Now playing: Cantina Song aka Mad About Mad About Me (From "Star Wars- Galaxy's Edge Oga's Cantina"-....mp3

R3X Command> stop music
Music playback stopped
```

## Mode-Specific Behavior

Music playback behaves differently depending on the current system mode:

* **IDLE Mode**: Music commands will automatically transition to Ambient Show Mode
* **AMBIENT Mode**: Full music controls with normal volume
* **INTERACTIVE Mode**: Music plays with automatic volume ducking during DJ R3X's speech

## Future Features

In future updates, music control will be integrated with voice commands, allowing you to ask DJ R3X to play music directly through conversation. 