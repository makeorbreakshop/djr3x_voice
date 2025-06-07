#!/bin/bash

# DJ R3X Dashboard Stop Script
# This script stops all dashboard system components

echo "🛑 Stopping DJ R3X Dashboard System..."
echo "====================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to stop a process by PID
stop_process() {
    local pid_file=$1
    local service_name=$2
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if [ ! -z "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo -e "${BLUE}Stopping $service_name (PID: $pid)...${NC}"
            kill "$pid"
            sleep 2
            
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                echo -e "${YELLOW}Force stopping $service_name...${NC}"
                kill -9 "$pid"
            fi
            echo -e "${GREEN}✅ $service_name stopped${NC}"
        else
            echo -e "${YELLOW}⚠️  $service_name not running${NC}"
        fi
        rm -f "$pid_file"
    else
        echo -e "${YELLOW}⚠️  No PID file for $service_name${NC}"
    fi
}

# Stop services in reverse order
if [ -d "logs" ]; then
    stop_process "logs/frontend_pid.txt" "Next.js Dashboard"
    stop_process "logs/bridge_pid.txt" "FastAPI Bridge"
    stop_process "logs/cantina_pid.txt" "CantinaOS"
else
    echo -e "${YELLOW}⚠️  No logs directory found${NC}"
fi

# Also kill any remaining processes by name
echo -e "${BLUE}Cleaning up any remaining processes...${NC}"

# Kill any remaining Next.js dev servers on port 3000
if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}Killing process on port 3000...${NC}"
    lsof -ti:3000 | xargs kill -9 2>/dev/null || true
fi

# Kill any remaining uvicorn servers on port 8000
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}Killing process on port 8000...${NC}"
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
fi

# Kill any remaining CantinaOS processes
pkill -f "cantina_os.main" 2>/dev/null || true

echo ""
echo -e "${GREEN}🏁 All services stopped successfully!${NC}"
echo "====================================="