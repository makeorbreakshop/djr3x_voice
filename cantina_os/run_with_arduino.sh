#!/bin/bash

# Script to run CantinaOS with Arduino LED controller enabled
# Make sure your Arduino is connected before running this script

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if we're running from the correct directory
if [ ! -d "cantina_os" ]; then
    echo -e "${YELLOW}Please run this script from the project root directory${NC}"
    exit 1
fi

# Check for connected Arduino devices
echo -e "${GREEN}Checking for connected Arduino devices...${NC}"
DEVICES=$(ls -la /dev/cu.usb* 2>/dev/null || echo "No devices found")
echo "$DEVICES"

# Determine the Arduino port
if echo "$DEVICES" | grep -q "cu.usbmodem"; then
    PORT=$(echo "$DEVICES" | grep "cu.usbmodem" | head -n 1 | awk '{print $NF}')
    echo -e "${GREEN}Found Arduino at $PORT${NC}"
else
    echo -e "${RED}No Arduino devices found. Please connect your Arduino and try again.${NC}"
    echo -e "${YELLOW}Do you want to continue in mock mode? (y/n)${NC}"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        PORT="none"
        echo -e "${YELLOW}Continuing in mock mode${NC}"
    else
        exit 1
    fi
fi

# Set environment variables for the Arduino connection
if [ "$PORT" == "none" ]; then
    export MOCK_LED_CONTROLLER="true"
    echo -e "${YELLOW}Running in MOCK mode (no hardware connection)${NC}"
else
    export MOCK_LED_CONTROLLER="false"
    export ARDUINO_SERIAL_PORT="$PORT"
    echo -e "${GREEN}Setting ARDUINO_SERIAL_PORT=$PORT${NC}"
fi

export ARDUINO_BAUD_RATE="115200"

# Test Arduino connection if not in mock mode
if [ "$MOCK_LED_CONTROLLER" == "false" ]; then
    echo -e "${GREEN}Testing Arduino connection...${NC}"
    # Run test script from correct directory
    cd "$(dirname "$0")"
    python -m tools.test_arduino_connection --port "$ARDUINO_SERIAL_PORT" --timeout 5.0
    TEST_RESULT=$?
    
    if [ $TEST_RESULT -ne 0 ]; then
        echo -e "${RED}Arduino connection test failed.${NC}"
        echo -e "${YELLOW}Do you want to continue in mock mode? (y/n)${NC}"
        read -r response
        if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
            export MOCK_LED_CONTROLLER="true"
            echo -e "${YELLOW}Continuing in mock mode${NC}"
        else
            exit 1
        fi
    else
        echo -e "${GREEN}Arduino connection test successful!${NC}"
    fi
fi

# Set higher log level for debugging
export LOG_LEVEL="DEBUG"

echo -e "${GREEN}Starting CantinaOS with Arduino LED controller${NC}"
echo -e "${GREEN}Configuration:${NC}"
echo -e "  MOCK_LED_CONTROLLER: ${MOCK_LED_CONTROLLER}"
echo -e "  ARDUINO_SERIAL_PORT: ${ARDUINO_SERIAL_PORT}"
echo -e "  ARDUINO_BAUD_RATE: ${ARDUINO_BAUD_RATE}"
echo -e "  LOG_LEVEL: ${LOG_LEVEL}"

# Run the program
python -m cantina_os.main

# Note: If you encounter permission issues with the serial port, you may need to:
# 1. Add your user to the dialout group (Linux): sudo usermod -a -G dialout $USER
# 2. Or run with sudo: sudo python -m cantina_os.main
# 3. On macOS, you might need to grant permissions in System Preferences > Security & Privacy 