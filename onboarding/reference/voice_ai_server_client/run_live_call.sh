#!/usr/bin/env bash
# Start a live voice call: streams your mic to the server and plays back
# replies. Runs setup.sh automatically if the venv is missing.
# Usage: ./run_live_call.sh [--list-devices] [--input-device N] [--output-device N]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "[run_live_call] no venv found, running setup.sh first ..."
    ./setup.sh
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

exec python live_call.py "$@"
