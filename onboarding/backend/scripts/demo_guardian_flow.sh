#!/usr/bin/env bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"
cd "$REPO_ROOT"
# Phase 9 demo: minor-customer application -- shows the main session
# blocking/waiting at the guardian step, a guardian link/token being
# generated, and a separate scoped session (opened via that token) resolving
# the guardian requirements, after which the main flow can proceed.
set -e
BASE="${BASE_URL:-http://127.0.0.1:8000}"
MOBILE="${1:-9876543212}"
GUARDIAN_MOBILE="${2:-9876543213}"

echo "== 1. start minor application =="
START=$(curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"minor_savings_account\", \"channel\": \"web\", \"language\": \"en\"}")
echo "$START" | python3 -m json.tool
APP_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['application']['id'])")
SESSION_ID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

echo "== 2. verify minor's own mobile + OTP (required independently of guardian) =="
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

echo "== 3. main session next prompt should now be blocked on guardian step (get_next_requirement(scope=None) is None or non-guardian) =="
curl -s "$BASE/applications/$APP_ID/status" | python3 -m json.tool

echo "== 4. generate guardian link =="
LINK_RESP=$(curl -s -X POST "$BASE/applications/$APP_ID/guardian/link" -H "Content-Type: application/json" \
  -d "{\"mobile_number\": \"$GUARDIAN_MOBILE\", \"relationship\": \"parent\"}")
echo "$LINK_RESP" | python3 -m json.tool
TOKEN=$(echo "$LINK_RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

echo "== 5. guardian consumes the token via /applications/start (creates a scope=guardian session on the SAME application) =="
GSTART=$(curl -s -X POST "$BASE/applications/start" -H "Content-Type: application/json" \
  -d "{\"product_id\": \"minor_savings_account\", \"channel\": \"web\", \"handoff_token\": \"$TOKEN\"}")
echo "$GSTART" | python3 -m json.tool
GSESSION_ID=$(echo "$GSTART" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

echo "== 6. guardian session resolves guardian_consent =="
curl -s -X POST "$BASE/sessions/$GSESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"parent, I consent\"}" | python3 -m json.tool

echo "== 7. guardian session resolves guardian_mobile_otp =="
curl -s -X POST "$BASE/sessions/$GSESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"$GUARDIAN_MOBILE\"}" | python3 -m json.tool
GCODE=$(python3 - "$APP_ID" << 'PYEOF'
import sys, hashlib
from datetime import datetime, timedelta
from backend.models.db import SessionLocal
from backend.models import models as m
db = SessionLocal()
app = db.query(m.Application).filter_by(id=sys.argv[1]).first()
req = next(r for r in app.requirements if r.type == "guardian_mobile_otp")
code = "654321"
req.otp_code_hash = hashlib.sha256(code.encode()).hexdigest()
req.otp_expires_at = datetime.utcnow() + timedelta(seconds=300)
db.commit()
print(code)
PYEOF
)
curl -s -X POST "$BASE/sessions/$GSESSION_ID/message" -H "Content-Type: application/json" -d "{\"text\": \"$GCODE\"}" | python3 -m json.tool

echo "== 8. main flow can now proceed past the guardian step =="
curl -s "$BASE/applications/$APP_ID" | python3 -m json.tool

echo "DEMO COMPLETE: application_id=$APP_ID"
