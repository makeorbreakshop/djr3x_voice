# DJ Tab Frontend Mockup Specification for v0

## Project Context

We're building a DJ Mode control interface for DJ R3X, a Star Wars-themed voice assistant that can act as an automated radio DJ. The interface should feel like a professional radio station control board - minimal controls with clear status indicators showing the automated process.

## Design Requirements

### Visual Theme
- **Star Wars holographic terminal aesthetic** 
- Dark background with glowing cyan/blue accents
- Monospace fonts for that terminal feel
- Subtle animations and glow effects
- Status indicators should pulse/glow when active

### Component Library
- Built with React and TypeScript
- Use Tailwind CSS for styling
- Incorporate Star Wars-inspired design elements
- Should match the existing dashboard tabs (Monitor, Voice, Music, System)

## Core Interface Elements

### 1. Header Section
```
DJ MODE: ● ON
```
- Large, clear status indicator at the top
- Red dot (●) when OFF, green/cyan dot when ON
- Should pulse/glow when active
- Font: Large, bold, monospace

### 2. Control Panel (3 Buttons)
```
[▶ START DJ]  [■ STOP DJ]  [⏭ NEXT TRACK]
```
- **START DJ Button**
  - Green/cyan when available to click
  - Disabled/grayed out when DJ mode is already running
  - Shows loading spinner when command is processing
  - Icon: Play symbol (▶)

- **STOP DJ Button**  
  - Red/orange when DJ mode is active
  - Disabled/grayed out when DJ mode is not running
  - Icon: Stop symbol (■)

- **NEXT TRACK Button**
  - Blue/cyan when available
  - Disabled when DJ mode is off or transitioning
  - Shows brief loading state when clicked
  - Icon: Skip/next symbol (⏭)

### 3. Now Playing Section
```
NOW PLAYING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎵 Cantina Band - Figrin D'an & Modal Nodes
▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░  2:15 / 3:30
```
- Track title and artist in larger text
- Progress bar showing current position
- Time display (current / total)
- Progress bar should be smooth and update in real-time
- Use cyan/blue for the filled portion

### 4. Status Grid
```
┌─────────────────────┬─────────────────────┐
│ UP NEXT             │ VOICE TRACK         │
│ ✅ Track Loaded     │ ✅ Ready            │
│ "Jedi Rocks"        │ Generated 30s ago   │
└─────────────────────┴─────────────────────┘
```

**UP NEXT Panel:**
- Shows next track title when loaded
- Status indicators:
  - ⏳ Loading... (yellow, animated)
  - ✅ Track Loaded (green)
  - ❌ Not Ready (red)
- Subtle glow effect when track is ready

**VOICE TRACK Panel:**
- Shows AI commentary status
- Status indicators:
  - 🎙️ Generating... (yellow, pulsing)
  - ✅ Ready (green)
  - ❌ Not Available (red)
- Shows how long ago it was generated
- Brief preview text of commentary (optional)

### 5. Process Status Bar
```
Status: 🎵 Playing music → 🎙️ Voice track in 15s
```
- Single line showing current activity
- Updates in real-time with countdown
- Status examples:
  - "🎵 Playing music → 🎙️ Voice track in 15s"
  - "🎙️ Playing voice track..."
  - "🔄 Transitioning to next track..."
  - "⏸️ DJ Mode idle - press START to begin"
  - "🚀 Starting DJ Mode..."
  - "🛑 Stopping DJ Mode..."

### 6. Session Info (Bottom Corner)
```
ON AIR: 00:15:32 | Tracks Played: 4
```
- Small, unobtrusive session timer
- Track counter
- Only visible when DJ mode is active

## State Variations

### DJ Mode OFF State
- Header shows "DJ MODE: ● OFF" (red dot)
- Only START button is enabled
- Now Playing section shows last played track or placeholder
- Status panels show "Not Active" 
- Process status shows "⏸️ DJ Mode idle - press START to begin"

### DJ Mode ON State
- Header shows "DJ MODE: ● ON" (green pulsing dot)
- STOP and NEXT buttons enabled, START disabled
- All sections actively updating
- Session timer running

### Transitioning State
- All buttons temporarily disabled
- Process status shows transition message
- Progress indicators animate

### Error States
- Red warning banner appears at top for errors
- Affected components show error state
- Clear error messages like:
  - "⚠️ Connection to DJ service lost"
  - "❌ Failed to load next track"
  - "⚠️ Voice generation service unavailable"

## Layout Mockup
```
┌─────────────────────────────────────────────────────┐
│                    DJ MODE: ● ON                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│     [▶ START DJ]  [■ STOP DJ]  [⏭ NEXT TRACK]     │
│                                                     │
├─────────────────────────────────────────────────────┤
│ NOW PLAYING                                         │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│ 🎵 Cantina Band - Figrin D'an & Modal Nodes        │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░  2:15 / 3:30              │
│                                                     │
├─────────────────────┬───────────────────────────────┤
│ UP NEXT             │ VOICE TRACK                   │
│ ✅ Track Loaded     │ ✅ Ready                      │
│ "Jedi Rocks"        │ Generated 30s ago             │
├─────────────────────┴───────────────────────────────┤
│ Status: 🎵 Playing music → 🎙️ Voice track in 15s   │
├─────────────────────────────────────────────────────┤
│                          ON AIR: 00:15:32 | Tracks: 4│
└─────────────────────────────────────────────────────┘
```

## Animation & Interaction Details

### Button Interactions
- Hover: Subtle glow effect
- Click: Brief scale down animation
- Loading: Replace icon with spinner
- Disabled: 50% opacity, no hover effects

### Status Indicators
- Active/ON: Gentle pulsing glow
- Loading: Spinning or pulsing animation
- Error: Red flash then steady red

### Progress Bar
- Smooth updates every second
- Slight glow on the filled portion
- Hover shows exact time tooltip

### Transitions
- Fade in/out for status changes
- Smooth color transitions
- No jarring movements

## Color Palette
- Background: #0A0E1A (dark blue-black)
- Primary (cyan): #00D4FF
- Success (green): #00FF88
- Warning (yellow): #FFD700
- Error (red): #FF3366
- Text: #E0E0E0
- Muted text: #888888
- Panel borders: #1A2332

## Typography
- Headers: 'Orbitron' or similar futuristic font
- Body: 'JetBrains Mono' or 'Fira Code'
- Status: Bold weight for emphasis
- Size hierarchy: 
  - Header: 24px
  - Buttons: 16px
  - Status: 14px
  - Small text: 12px

## Responsive Behavior
- Minimum width: 600px
- Stack status panels vertically on narrow screens
- Maintain button visibility at all sizes
- Scale fonts appropriately

## Accessibility
- All buttons keyboard accessible
- Clear focus indicators
- ARIA labels for screen readers
- Sufficient color contrast
- Status changes announced to screen readers

## Example Implementation Notes for v0

```typescript
// Example state structure
interface DJTabState {
  djModeActive: boolean;
  isTransitioning: boolean;
  currentTrack: {
    title: string;
    artist: string;
    duration: number;
    position: number;
  };
  nextTrack: {
    status: 'loading' | 'ready' | 'error';
    title?: string;
  };
  voiceTrack: {
    status: 'generating' | 'ready' | 'error';
    generatedAt?: Date;
  };
  processStatus: string;
  sessionStartTime?: Date;
  tracksPlayed: number;
}

// Example component structure
<DJTab>
  <Header djModeActive={djModeActive} />
  <ControlPanel 
    onStart={handleStart}
    onStop={handleStop}
    onNext={handleNext}
    djModeActive={djModeActive}
    isTransitioning={isTransitioning}
  />
  <NowPlaying track={currentTrack} />
  <StatusGrid 
    nextTrack={nextTrack}
    voiceTrack={voiceTrack}
  />
  <ProcessStatus status={processStatus} />
  <SessionInfo 
    startTime={sessionStartTime}
    tracksPlayed={tracksPlayed}
  />
</DJTab>
```

## Key Points for v0
1. This is a radio station control board - professional, minimal, status-focused
2. Only 3 buttons that do one thing each
3. Visual feedback is critical - users need to see what's happening
4. Star Wars theme but functional first
5. Real-time updates are important for the "live" feel
6. Error states should be clear but not alarming
7. The interface should feel responsive and alive with subtle animations

Remember: This is for an automated DJ system where most of the work happens in the background. The interface just provides control and visibility into the process.