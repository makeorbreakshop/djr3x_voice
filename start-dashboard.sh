#!/bin/bash

# DJ R3X Dashboard Startup Script
# This script starts all three components of the dashboard system

set -e  # Exit on any error

# Parse command line arguments
FORCE_CLEANUP=false
AUTO_CLEANUP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE_CLEANUP=true
            shift
            ;;
        --auto-cleanup|-a)
            AUTO_CLEANUP=true
            shift
            ;;
        --help|-h)
            echo "DJ R3X Dashboard Startup Script"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -f, --force        Force cleanup existing processes without prompting"
            echo "  -a, --auto-cleanup Automatically cleanup if ports are in use"
            echo "  -h, --help         Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                    # Start normally, exit if ports in use"
            echo "  $0 --force           # Always cleanup before starting"
            echo "  $0 --auto-cleanup    # Cleanup only if needed"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "🤖 Starting DJ R3X Dashboard System..."
echo "======================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to check if a port is in use
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null ; then
        echo "Port $port is already in use"
        return 1
    fi
    return 0
}

# Function to kill process on a specific port
kill_port() {
    local port=$1
    local service_name=$2
    
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${YELLOW}🔄 Killing existing process on port $port ($service_name)...${NC}"
        lsof -ti:$port | xargs kill -9 2>/dev/null || true
        sleep 2
        
        # Verify the port is now free
        if ! lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo -e "${GREEN}✅ Port $port cleared${NC}"
        else
            echo -e "${RED}❌ Failed to clear port $port${NC}"
            return 1
        fi
    fi
    return 0
}

# Function to cleanup all dashboard-related processes
cleanup_existing_processes() {
    echo -e "${BLUE}🧹 Cleaning up existing dashboard processes...${NC}"
    
    # Kill any remaining Next.js dev servers on port 3000
    kill_port 3000 "Next.js Dashboard"
    
    # Kill any remaining uvicorn servers on port 8000
    kill_port 8000 "FastAPI Bridge"
    
    # Kill any remaining CantinaOS processes
    if pgrep -f "cantina_os.main" > /dev/null; then
        echo -e "${YELLOW}🔄 Stopping existing CantinaOS processes...${NC}"
        pkill -f "cantina_os.main" 2>/dev/null || true
        sleep 2
        echo -e "${GREEN}✅ CantinaOS processes cleared${NC}"
    fi
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check prerequisites
echo -e "${BLUE}Checking prerequisites...${NC}"

if ! command_exists node; then
    echo -e "${RED}❌ Node.js not found. Please install Node.js 18+${NC}"
    exit 1
fi

if ! command_exists python3; then
    echo -e "${RED}❌ Python3 not found. Please install Python 3.11+${NC}"
    exit 1
fi

if ! command_exists npm; then
    echo -e "${RED}❌ npm not found. Please install npm${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All prerequisites found${NC}"

# Handle port cleanup based on options
if $FORCE_CLEANUP; then
    cleanup_existing_processes
elif $AUTO_CLEANUP; then
    # Check if ports are available and cleanup if needed
    echo -e "${BLUE}Checking ports...${NC}"
    PORTS_IN_USE=false
    
    if ! check_port 3000; then
        PORTS_IN_USE=true
    fi
    
    if ! check_port 8000; then
        PORTS_IN_USE=true
    fi
    
    if $PORTS_IN_USE; then
        echo -e "${YELLOW}⚠️  Ports are in use, automatically cleaning up...${NC}"
        cleanup_existing_processes
    fi
else
    # Original behavior - check ports and exit if in use
    echo -e "${BLUE}Checking ports...${NC}"
    
    PORTS_BLOCKED=false
    
    if ! check_port 3000; then
        echo -e "${RED}❌ Port 3000 (Next.js) is in use${NC}"
        PORTS_BLOCKED=true
    fi
    
    if ! check_port 8000; then
        echo -e "${RED}❌ Port 8000 (FastAPI Bridge) is in use${NC}"
        PORTS_BLOCKED=true
    fi
    
    if $PORTS_BLOCKED; then
        echo ""
        echo -e "${YELLOW}💡 TIP: Use one of these options to automatically cleanup:${NC}"
        echo "   ./start-dashboard.sh --force           # Always cleanup first"
        echo "   ./start-dashboard.sh --auto-cleanup    # Cleanup only if needed"
        echo "   ./stop-dashboard.sh                    # Stop existing services first"
        echo ""
        exit 1
    fi
fi

# Final verification that ports are now available
echo -e "${BLUE}Verifying ports are available...${NC}"
if ! check_port 3000 || ! check_port 8000; then
    echo -e "${RED}❌ Failed to clear required ports${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Ports 3000 and 8000 are available${NC}"

# Create log directory
mkdir -p logs

# Start CantinaOS with integrated Web Bridge
echo -e "${BLUE}Starting CantinaOS with Web Bridge...${NC}"
if ! pgrep -f "cantina_os.main" > /dev/null; then
    cd cantina_os
    
    # Install requirements if needed
    if [ ! -f ".requirements_installed" ]; then
        echo -e "${YELLOW}Installing CantinaOS dependencies...${NC}"
        pip install -r requirements.txt
        touch .requirements_installed
    fi
    
    python -m cantina_os.main > ../logs/cantina_os.log 2>&1 &
    CANTINA_PID=$!
    cd ..
    echo -e "${GREEN}✅ CantinaOS with Web Bridge started (PID: $CANTINA_PID)${NC}"
    sleep 5  # Give CantinaOS and Web Bridge time to initialize
else
    echo -e "${YELLOW}⚠️  CantinaOS already running${NC}"
    CANTINA_PID=""
fi

# Start Next.js Frontend in background
echo -e "${BLUE}Starting Next.js Dashboard...${NC}"
cd dj-r3x-dashboard

# Install npm dependencies if needed
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installing Node.js dependencies...${NC}"
    npm install
fi

# Start Next.js development server
npm run dev > ../logs/frontend.log 2>&1 &
FRONTEND_PID=$!
cd ..
echo -e "${GREEN}✅ Next.js Dashboard started (PID: $FRONTEND_PID)${NC}"

# Save PIDs for cleanup
echo "$CANTINA_PID" > logs/cantina_pid.txt
echo "$FRONTEND_PID" > logs/frontend_pid.txt

echo ""
echo -e "${GREEN}🚀 All services started successfully!${NC}"
echo "======================================"
echo -e "${BLUE}Dashboard URL:${NC} http://localhost:3000"
echo -e "${BLUE}Bridge API:${NC}    http://localhost:8000 (integrated with CantinaOS)"
echo -e "${BLUE}Bridge Docs:${NC}   http://localhost:8000/docs"
echo ""
echo -e "${YELLOW}Logs are saved in the 'logs/' directory:${NC}"
echo "  - logs/cantina_os.log (CantinaOS + Web Bridge)"
echo "  - logs/frontend.log (Next.js)"
echo ""
echo -e "${YELLOW}To stop all services, run:${NC} ./stop-dashboard.sh"
echo ""

# Wait a moment for services to start
echo "Waiting for services to initialize..."
sleep 5

# Check if services are running
echo -e "${BLUE}Service Status:${NC}"

if curl -s http://localhost:8000 > /dev/null; then
    echo -e "${GREEN}✅ Bridge Service (Port 8000)${NC}"
else
    echo -e "${RED}❌ Bridge Service (Port 8000)${NC}"
fi

if curl -s http://localhost:3000 > /dev/null; then
    echo -e "${GREEN}✅ Dashboard (Port 3000)${NC}"
else
    echo -e "${RED}❌ Dashboard (Port 3000)${NC}"
fi

echo ""
echo -e "${GREEN}🎉 Ready! Open http://localhost:3000 in your browser${NC}"