#!/usr/bin/env bash
# Upload a local audio file and print the transcript.
# Runs setup.sh automatically if the venv is missing.
# Usage: ./run_transcribe.sh --file sample.wav [--language ta]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "[run_transcribe] no venv found, running setup.sh first ..."
    ./setup.sh
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

exec python transcribe_file.py "$@"
