#!/bin/bash

# DJ R3X Dashboard Health Check Script
# Verifies all services are running and connecting properly

echo "🔍 DJ R3X Dashboard Health Check"
echo "================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to check HTTP endpoint
check_http() {
    local url=$1
    local service_name=$2
    
    if curl -s --max-time 5 "$url" > /dev/null; then
        echo -e "${GREEN}✅ $service_name${NC} - $url"
        return 0
    else
        echo -e "${RED}❌ $service_name${NC} - $url"
        return 1
    fi
}

# Function to check if process is running
check_process() {
    local pattern=$1
    local service_name=$2
    
    if pgrep -f "$pattern" > /dev/null; then
        echo -e "${GREEN}✅ $service_name Process${NC}"
        return 0
    else
        echo -e "${RED}❌ $service_name Process${NC}"
        return 1
    fi
}

# Function to check port
check_port() {
    local port=$1
    local service_name=$2
    
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${GREEN}✅ $service_name Port ($port)${NC}"
        return 0
    else
        echo -e "${RED}❌ $service_name Port ($port)${NC}"
        return 1
    fi
}

echo -e "${BLUE}Checking Processes...${NC}"
check_process "cantina_os.main" "CantinaOS"
check_process "uvicorn.*main:app" "FastAPI Bridge"
check_process "next.*dev" "Next.js Dashboard"

echo ""
echo -e "${BLUE}Checking Ports...${NC}"
check_port 3000 "Next.js"
check_port 8000 "FastAPI Bridge"

echo ""
echo -e "${BLUE}Checking HTTP Endpoints...${NC}"
check_http "http://localhost:8000" "FastAPI Bridge"
check_http "http://localhost:3000" "Next.js Dashboard"

echo ""
echo -e "${BLUE}Checking API Health...${NC}"

# Check bridge API status
if curl -s http://localhost:8000 | grep -q "DJ R3X Web Bridge"; then
    echo -e "${GREEN}✅ Bridge Service API${NC}"
else
    echo -e "${RED}❌ Bridge Service API${NC}"
fi

# Check bridge docs
if curl -s http://localhost:8000/docs > /dev/null; then
    echo -e "${GREEN}✅ Bridge API Documentation${NC}"
else
    echo -e "${RED}❌ Bridge API Documentation${NC}"
fi

echo ""
echo -e "${BLUE}Checking Log Files...${NC}"

if [ -f "logs/cantina_os.log" ]; then
    echo -e "${GREEN}✅ CantinaOS Log${NC} ($(wc -l < logs/cantina_os.log) lines)"
else
    echo -e "${RED}❌ CantinaOS Log${NC}"
fi

if [ -f "logs/bridge.log" ]; then
    echo -e "${GREEN}✅ Bridge Log${NC} ($(wc -l < logs/bridge.log) lines)"
else
    echo -e "${RED}❌ Bridge Log${NC}"
fi

if [ -f "logs/frontend.log" ]; then
    echo -e "${GREEN}✅ Frontend Log${NC} ($(wc -l < logs/frontend.log) lines)"
else
    echo -e "${RED}❌ Frontend Log${NC}"
fi

echo ""
echo -e "${BLUE}Quick Log Tail (last 3 lines each):${NC}"

if [ -f "logs/cantina_os.log" ]; then
    echo -e "${YELLOW}CantinaOS:${NC}"
    tail -3 logs/cantina_os.log 2>/dev/null || echo "  (empty log)"
fi

if [ -f "logs/bridge.log" ]; then
    echo -e "${YELLOW}Bridge:${NC}"
    tail -3 logs/bridge.log 2>/dev/null || echo "  (empty log)"
fi

echo ""
echo -e "${BLUE}System Resources:${NC}"
echo -e "${YELLOW}CPU Usage:${NC} $(top -l 1 | grep "CPU usage" | awk '{print $3}' | cut -d% -f1)%"
echo -e "${YELLOW}Memory:${NC} $(top -l 1 | grep "PhysMem" | awk '{print $2}' | cut -d/ -f1) used"

echo ""
echo -e "${GREEN}🎉 Health check complete!${NC}"
echo "================================"
echo -e "${BLUE}Next steps:${NC}"
echo "1. Open http://localhost:3000 to access dashboard"
echo "2. Check that all tabs are working"
echo "3. Test voice recording functionality"
echo "4. Try music playback controls"
echo ""
echo -e "${YELLOW}For detailed logs:${NC}"
echo "  tail -f logs/cantina_os.log"
echo "  tail -f logs/bridge.log"
echo "  tail -f logs/frontend.log"