# DJ R3X Dashboard - Quick Setup Guide

## 🚀 Quick Start (2 commands)

```bash
# 1. Make scripts executable (first time only)
chmod +x start-dashboard.sh stop-dashboard.sh

# 2. Start everything
./start-dashboard.sh
```

**That's it!** Open http://localhost:3000 in your browser.

## 🛑 To Stop

```bash
./stop-dashboard.sh
```

## 📋 What Gets Started

The startup script automatically handles:

1. **CantinaOS** - Main voice assistant system
2. **FastAPI Bridge** - WebSocket bridge service (port 8000)  
3. **Next.js Dashboard** - Web interface (port 3000)

All services start in the background with logs saved to `logs/` directory.

## 🔧 Prerequisites

- **Node.js 18+** with npm
- **Python 3.11+** with pip
- **Modern browser** (Chrome 90+)

## 📊 Service URLs

- **Dashboard**: http://localhost:3000
- **Bridge API**: http://localhost:8000  
- **API Docs**: http://localhost:8000/docs

## 🔍 Troubleshooting

### Port Already in Use
```bash
# Check what's using the ports
lsof -i :3000  # Next.js
lsof -i :8000  # FastAPI Bridge

# Kill processes if needed
./stop-dashboard.sh
```

### Services Not Connecting
```bash
# Check logs
tail -f logs/cantina_os.log
tail -f logs/bridge.log  
tail -f logs/frontend.log
```

### Fresh Start
```bash
# Stop everything and clear caches
./stop-dashboard.sh
rm -rf dj-r3x-dashboard/node_modules
rm -rf dj-r3x-bridge/venv
rm -rf logs/
./start-dashboard.sh
```

## 🎯 Dashboard Features

### ✅ MONITOR Tab
- Real-time service status
- Audio spectrum visualization
- Live transcription feed

### ✅ VOICE Tab  
- Voice recording controls
- Processing pipeline status
- Confidence scores

### ✅ MUSIC Tab
- 20+ Star Wars cantina tracks
- Playback controls with queue
- Volume control with ducking

### ✅ DJ MODE Tab
- DJ mode activation/control
- Auto-transition settings
- Commentary monitoring

### ✅ SYSTEM Tab
- Service health monitoring
- Real-time event logs
- Performance metrics

## 🔗 Architecture

```
Browser (localhost:3000)
    ↕ Socket.io/HTTP
FastAPI Bridge (localhost:8000) 
    ↕ Event Bus
CantinaOS (Voice Assistant)
```

The bridge service translates between web dashboard events and CantinaOS internal events, enabling real-time control and monitoring.