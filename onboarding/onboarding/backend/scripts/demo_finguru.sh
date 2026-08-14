#!/usr/bin/env bash
# FinGuru Phase 2 demo: exercises the grounded chat engine end-to-end.
#   1. a well-covered question   -> grounded answer + citations
#   2. an uncovered question     -> not_covered (triggers Phase 4 gap-filling)
#   3. a follow-up in the same conversation
#
# Requires the server running (uvicorn backend.main:app) and the knowledge base
# seeded (python -m backend.scripts.seed_finguru_knowledge).
# Override the host with BASE_URL, e.g. BASE_URL=http://localhost:8001 bash ...
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
BASE="${BASE_URL:-http://127.0.0.1:8000}"
PY="${PYTHON:-python}"

echo "== 1. start conversation =="
START=$(curl -s -X POST "$BASE/finguru/conversations/start" -H "Content-Type: application/json" -d '{}')
echo "$START" | $PY -m json.tool
CONV_ID=$(echo "$START" | $PY -c "import sys,json;print(json.load(sys.stdin)['conversation_id'])")
echo "conversation_id=$CONV_ID"

echo
echo "== 2. GROUNDED question: 'How does the Sukanya Samriddhi Yojana work?' =="
curl -s -X POST "$BASE/finguru/conversations/$CONV_ID/message" -H "Content-Type: application/json" \
  -d '{"text": "How does the Sukanya Samriddhi Yojana work and what interest does it pay?"}' | $PY -m json.tool

echo
echo "== 3. NOT_COVERED question: 'latest update on the upcoming tech merger?' =="
curl -s -X POST "$BASE/finguru/conversations/$CONV_ID/message" -H "Content-Type: application/json" \
  -d '{"text": "Whats the latest update on the upcoming tech merger between two US startups?"}' | $PY -m json.tool

echo
echo "== 4. FOLLOW-UP (same conversation): 'Who is eligible to open one?' =="
curl -s -X POST "$BASE/finguru/conversations/$CONV_ID/message" -H "Content-Type: application/json" \
  -d '{"text": "Who is eligible to open one and what is the minimum deposit?"}' | $PY -m json.tool

echo
echo "== 5. full conversation history =="
curl -s "$BASE/finguru/conversations/$CONV_ID" | $PY -m json.tool

echo
echo "DEMO COMPLETE: conversation_id=$CONV_ID"
