#!/bin/bash
# 
# DJ R3X CantinaOS TypeScript Schema Generator
#
# Generates TypeScript interfaces from Python Pydantic models to ensure
# type safety across the web dashboard and backend API boundaries.
#
# Usage:
#   ./scripts/generate-schemas.sh [--watch] [--verbose]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANTINA_ROOT="$(dirname "$SCRIPT_DIR")"
DASHBOARD_ROOT="$(dirname "$CANTINA_ROOT")/dj-r3x-dashboard"

echo -e "${BLUE}DJ R3X TypeScript Schema Generator${NC}"
echo "=================================================="
echo -e "Cantina Root: ${CANTINA_ROOT}"
echo -e "Dashboard Root: ${DASHBOARD_ROOT}"
echo

# Check if we're in the right directory
if [[ ! -f "$CANTINA_ROOT/cantina_os/schemas/web_commands.py" ]]; then
    echo -e "${RED}❌ Error: Cannot find web_commands.py schema file${NC}"
    echo "   Make sure you're running from the cantina_os directory"
    exit 1
fi

# Check if dashboard directory exists
if [[ ! -d "$DASHBOARD_ROOT" ]]; then
    echo -e "${RED}❌ Error: Dashboard directory not found: $DASHBOARD_ROOT${NC}"
    echo "   Make sure the dj-r3x-dashboard directory exists"
    exit 1
fi

# Check Python environment
echo -e "${YELLOW}🔍 Checking Python environment...${NC}"

# Check if virtual environment is activated
if [[ -z "$VIRTUAL_ENV" ]] && [[ ! -f "$CANTINA_ROOT/venv/bin/activate" ]]; then
    echo -e "${YELLOW}⚠️  No virtual environment detected${NC}"
    echo "   Consider activating venv: source venv/bin/activate"
fi

# Check if required packages are available
python3 -c "import pydantic, cantina_os.schemas.web_commands" 2>/dev/null
if [[ $? -ne 0 ]]; then
    echo -e "${RED}❌ Error: Required Python packages not found${NC}"
    echo "   Make sure Pydantic and CantinaOS are installed:"
    echo "   pip install -r requirements.txt"
    exit 1
fi

echo -e "${GREEN}✅ Python environment ready${NC}"
echo

# Run the TypeScript generator
echo -e "${YELLOW}🔄 Generating TypeScript schemas...${NC}"

cd "$CANTINA_ROOT"

python3 scripts/generate_typescript_schemas.py "$@"

if [[ $? -eq 0 ]]; then
    echo
    echo -e "${GREEN}✅ Schema generation completed successfully${NC}"
    
    # Show generated file info
    SCHEMA_FILE="$DASHBOARD_ROOT/src/types/schemas.ts"
    if [[ -f "$SCHEMA_FILE" ]]; then
        echo -e "📄 Generated file: ${SCHEMA_FILE}"
        echo -e "📊 File size: $(wc -c < "$SCHEMA_FILE") bytes"
        echo -e "📝 Lines: $(wc -l < "$SCHEMA_FILE") lines"
        
        # Show a preview of generated types
        echo
        echo -e "${BLUE}📋 Generated Types Preview:${NC}"
        echo "=================================================="
        grep -E "^export (interface|enum)" "$SCHEMA_FILE" | head -10
        
        if [[ $(grep -c "^export" "$SCHEMA_FILE") -gt 10 ]]; then
            echo "... and $(( $(grep -c "^export" "$SCHEMA_FILE") - 10 )) more types"
        fi
    fi
    
    echo
    echo -e "${GREEN}🎯 Next Steps:${NC}"
    echo "   1. Review generated schemas in: src/types/schemas.ts"
    echo "   2. Update useSocket.ts hook to use new types"
    echo "   3. Run npm build to verify TypeScript compilation"
    echo "   4. Test type safety in dashboard components"
    
else
    echo -e "${RED}❌ Schema generation failed${NC}"
    exit 1
fi