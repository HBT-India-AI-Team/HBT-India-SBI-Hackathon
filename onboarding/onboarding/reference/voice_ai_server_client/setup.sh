#!/usr/bin/env bash
# One-time (or re-run-anytime) client setup: create a venv, install the
# lightweight client dependencies, and create client/.env from the example
# if it doesn't exist yet. Run this on your laptop.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"

pick_python() {
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "$candidate"
            return
        fi
    done
    echo "error: no python3 found" >&2
    exit 1
}

if [ ! -d "$VENV_DIR" ]; then
    PYTHON_BIN="$(pick_python)"
    echo "[client setup] creating venv with $PYTHON_BIN at $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[client setup] installing dependencies ..."
pip install --upgrade pip -q
pip install -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "[client setup] created client/.env -- edit it now:"
    echo "    YONO_SERVER_URL      the LAN base URL the server printed on startup"
    echo "    YONO_SERVER_API_KEY  copy from the server's own .env"
else
    echo "[client setup] .env already exists, leaving it as-is"
fi

echo "[client setup] done. Try: ./run_live_call.sh"
