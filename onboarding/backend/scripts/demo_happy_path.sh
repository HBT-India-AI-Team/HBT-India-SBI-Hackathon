#!/usr/bin/env bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"
cd "$REPO_ROOT"
# Phase 3 demo: full savings-account application walked end to end via the
# REST API, proving the Requirement Graph + rule-based engine work together.
# Requires the server running: uvicorn backend.main:app --port 8000
set -e
BASE="${BASE_URL:-http://127.0.0.1:8000}"
MOBILE="${1:-9876543210}"

echo "== 1. start application =="
START=$(curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"savings_account\", \"channel\": \"web\", \"language\": \"en\"}")
echo "$START" | python3 -m json.tool
APP_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['application']['id'])")
SESSION_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
echo "application_id=$APP_ID session_id=$SESSION_ID"

echo "== 2. send mobile number =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" \
  -d "{\"text\": \"$MOBILE\"}" | python3 -m json.tool

echo "== 3. read OTP from server logs is manual in a real demo; using known test flow: submit 000000 will fail, so fetch generated code isn't exposed via API by design. Using debug bypass: any 6-digit code is accepted only if it matches; for automated demo we peek the DB. =="
CODE=$(python3 - "$APP_ID" << 'PYEOF'
import sys, hashlib
sys.path.insert(0, ".")
from backend.models.db import SessionLocal
from backend.models import models as m
db = SessionLocal()
app = db.query(m.Application).filter_by(id=sys.argv[1]).first()
req = next(r for r in app.requirements if r.type == "mobile_otp")
# demo-only: read back the plaintext OTP from the app log is not possible here,
# so directly set a known code+hash for deterministic scripted demo purposes.
code = "123456"
req.otp_code_hash = hashlib.sha256(code.encode()).hexdigest()
from datetime import datetime, timedelta
req.otp_expires_at = datetime.utcnow() + timedelta(seconds=300)
db.commit()
print(code)
PYEOF
)
echo "using OTP code: $CODE"

echo "== 4. submit OTP =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" \
  -d "{\"text\": \"$CODE\"}" | python3 -m json.tool

echo "== 5. submit PAN =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" \
  -d "{\"text\": \"ABCDE1234F\"}" | python3 -m json.tool

echo "== 6. get application detail (find document requirement id) =="
DETAIL=$(curl -s "$BASE/applications/$APP_ID")
echo "$DETAIL" | python3 -m json.tool
DOC_REQ_ID=$(echo "$DETAIL" | python3 -c "import sys,json;d=json.load(sys.stdin);print([r['id'] for r in d['requirements'] if r['type']=='document'][0])")

echo "== 7. upload document (debug_outcome=verify) =="
echo "sample pan card content" > /tmp/pan_card.txt
curl -s -X POST "$BASE/applications/$APP_ID/documents" \
  -F "requirement_id=$DOC_REQ_ID" -F "debug_outcome=verify" -F "file=@/tmp/pan_card.txt" | python3 -m json.tool

echo "== 8. confirm product =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" \
  -d "{\"text\": \"yes\"}" | python3 -m json.tool

echo "== 9. poll application until document requirement verifies (background scheduler) =="
for i in 1 2 3 4 5 6; do
  sleep 5
  STATUS=$(curl -s "$BASE/applications/$APP_ID" | python3 -c "import sys,json;d=json.load(sys.stdin);print([r['state'] for r in d['requirements'] if r['type']=='document'][0])")
  echo "poll $i: document requirement state = $STATUS"
  if [ "$STATUS" == "VERIFIED" ]; then break; fi
done

echo "== 10. submit for review =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" \
  -d "{\"text\": \"yes\"}" | python3 -m json.tool

echo "== 11. final application state =="
curl -s "$BASE/applications/$APP_ID" | python3 -m json.tool

echo "DEMO COMPLETE: application_id=$APP_ID"
