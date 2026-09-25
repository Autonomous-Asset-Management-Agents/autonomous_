#!/usr/bin/env bash
# setup.sh — AAAgents OSS First-Time Setup (macOS / Linux / Git Bash)
#
# This is a thin wrapper that invokes the Python-based setup script.
# The actual generation of cryptographically secure secrets and file I/O
# happens in setup.py to ensure cross-platform consistency and security.

set -euo pipefail

# Text colors
RED='\033[0;31m'
NC='\033[0m' # No Color

# Ensure we are in the root directory
if [ ! -f "setup.py" ]; then
    echo -e "${RED}❌ setup.py not found.${NC}"
    echo "   Make sure you are in the autonomous_ root directory."
    exit 1
fi

# Resolve a real Python 3.8+ interpreter
PYTHON=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 \
        && "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
        PYTHON="$cand"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}❌ Python 3.8+ not found.${NC}"
    echo "   Microsoft Store stubs (python3.exe redirector) do not count."
    echo "   Install Python 3 from https://www.python.org/downloads/ and retry."
    exit 1
fi

# Delegate to the robust Python setup script.
# Forwards all CLI args (e.g. --non-interactive for CI runs).
exec "$PYTHON" setup.py "$@"
