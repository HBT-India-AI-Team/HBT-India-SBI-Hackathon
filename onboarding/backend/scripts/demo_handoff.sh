#!/usr/bin/env bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"
cd "$REPO_ROOT"
# Phase 10 demo: "Continue on WhatsApp/Telegram" channel handoff.
#
# NOTE: this build does not have a live WhatsApp/Telegram webhook receiver
# (no earlier adapter work exists in this repo to reuse -- see
# /docs/ARCHITECTURE.md). What IS real and demonstrated here: token
# generation (POST /applications/{id}/handoff/{channel}), and token
# consumption resolving to the SAME Application via
# POST /applications/start with handoff_token set (the exact code path a
# real webhook receiver would call once it received the deep-link token
# from WhatsApp/Telegram). This simulates "the webhook received the token
# and called our normal consumption path" rather than a fake HTTP webhook
# payload, since no webhook route exists to receive one.
set -e
BASE="${BASE_URL:-http://127.0.0.1:8000}"
MOBILE="${1:-9876543214}"

echo "== 1. start a web application, verify mobile =="
START=$(curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"savings_account\", \"channel\": \"web\", \"language\": \"en\"}")
echo "$START" | python3 -m json.tool
APP_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['application']['id'])")
SESSION_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"$MOBILE\"}" > /dev/null

echo "== 2. generate a WhatsApp handoff token/link for this application =="
HANDOFF=$(curl -s -X POST "$BASE/applications/$APP_ID/handoff/whatsapp")
echo "$HANDOFF" | python3 -m json.tool
TOKEN=$(echo "$HANDOFF" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

echo "== 3. simulate the WhatsApp side consuming that token (as a webhook receiver would, calling the same consumption path) =="
RESUME=$(curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"savings_account\", \"channel\": \"whatsapp\", \"handoff_token\": \"$TOKEN\"}")
echo "$RESUME" | python3 -m json.tool
RESUMED_APP_ID=$(echo "$RESUME" | python3 -c "import sys,json;print(json.load(sys.stdin)['application']['id'])")

if [ "$RESUMED_APP_ID" == "$APP_ID" ]; then
  echo "OK: handoff resolved to the SAME application ($APP_ID) rather than creating a new one."
else
  echo "FAIL: expected $APP_ID, got $RESUMED_APP_ID"
  exit 1
fi

echo "== 4. confirm reusing the same token again fails (single-use) =="
curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"savings_account\", \"channel\": \"whatsapp\", \"handoff_token\": \"$TOKEN\"}" | python3 -m json.tool || true

echo "DEMO COMPLETE"
