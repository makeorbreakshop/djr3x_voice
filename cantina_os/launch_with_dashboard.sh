#!/bin/bash
# Launch DJ R3X with Textual Dashboard enabled
# This replaces the CLI with a visual TUI dashboard
#
# Usage: ./launch_with_dashboard.sh

cd "$(dirname "$0")/cantina_os"
ENABLE_TUI_DASHBOARD=true ../../venv/bin/python -m cantina_os.main
