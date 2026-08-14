#!/usr/bin/env bash
# Send text to the server and save/play the synthesized audio.
# Runs setup.sh automatically if the venv is missing.
# Usage: ./run_synthesize.sh --text "வணக்கம்" --language ta --play
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "[run_synthesize] no venv found, running setup.sh first ..."
    ./setup.sh
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

exec python synthesize_text.py "$@"
