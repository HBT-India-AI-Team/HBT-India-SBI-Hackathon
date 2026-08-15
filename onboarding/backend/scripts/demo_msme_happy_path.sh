#!/usr/bin/env bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"
cd "$REPO_ROOT"
# Phase 3 demo: MSME current-account application, proving the Requirement
# Graph genuinely varies by product (gstin/business_pan/authorized_signatory
# + two documents, not the savings-account set).
set -e
BASE="${BASE_URL:-http://127.0.0.1:8000}"
MOBILE="${1:-9876543211}"

echo "== 1. start MSME application =="
START=$(curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"msme_current_account\", \"channel\": \"web\", \"language\": \"en\"}")
echo "$START" | python3 -m json.tool
APP_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['application']['id'])")
SESSION_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
echo "application_id=$APP_ID session_id=$SESSION_ID"

echo "== 2. mobile + OTP =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"$MOBILE\"}" | python3 -m json.tool
CODE=$(python3 - "$APP_ID" << 'PYEOF'
import sys, hashlib
from datetime import datetime, timedelta
from backend.models.db import SessionLocal
from backend.models import models as m
db = SessionLocal()
app = db.query(m.Application).filter_by(id=sys.argv[1]).first()
req = next(r for r in app.requirements if r.type == "mobile_otp")
code = "123456"
req.otp_code_hash = hashlib.sha256(code.encode()).hexdigest()
req.otp_expires_at = datetime.utcnow() + timedelta(seconds=300)
db.commit()
print(code)
PYEOF
)
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"$CODE\"}" | python3 -m json.tool

echo "== 3. authorized signatory PAN =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"ABCDE1234F\"}" | python3 -m json.tool

echo "== 4. business PAN =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"FGHIJ5678K\"}" | python3 -m json.tool

echo "== 5. GSTIN =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"22AAAAA0000A1Z5\"}" | python3 -m json.tool

echo "== 6. authorized signatory name =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"Ramesh Kumar\"}" | python3 -m json.tool

echo "== 7. application detail (find document requirement ids) =="
DETAIL=$(curl -s "$BASE/applications/$APP_ID")
echo "$DETAIL" | python3 -m json.tool
DOC_IDS=$(echo "$DETAIL" | python3 -c "import sys,json;d=json.load(sys.stdin);print(' '.join(r['id'] for r in d['requirements'] if r['type']=='document'))")

echo "== 8. upload both documents =="
echo "sample pan card" > /tmp/pan_card.txt
echo "sample gst cert" > /tmp/gst_cert.txt
i=0
for DID in $DOC_IDS; do
  i=$((i+1))
  FILE="/tmp/pan_card.txt"
  if [ $i -eq 2 ]; then FILE="/tmp/gst_cert.txt"; fi
  curl -s -X POST "$BASE/applications/$APP_ID/documents" -F "requirement_id=$DID" -F "debug_outcome=verify" -F "file=@$FILE" | python3 -m json.tool
done

echo "== 9. confirm product =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"yes\"}" | python3 -m json.tool

echo "== 10. poll until documents verify =="
for i in 1 2 3 4 5 6; do
  sleep 5
  STATES=$(curl -s "$BASE/applications/$APP_ID" | python3 -c "import sys,json;d=json.load(sys.stdin);print([r['state'] for r in d['requirements'] if r['type']=='document'])")
  echo "poll $i: document states = $STATES"
  if [[ "$STATES" != *"VERIFYING"* ]]; then break; fi
done

echo "== 11. submit for review =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"yes\"}" | python3 -m json.tool

echo "== 12. final state =="
curl -s "$BASE/applications/$APP_ID" | python3 -m json.tool

echo "DEMO COMPLETE: application_id=$APP_ID"
