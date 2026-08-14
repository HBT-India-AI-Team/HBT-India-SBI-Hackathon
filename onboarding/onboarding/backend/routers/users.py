"""
/backend/routers/users.py -- Phase 12 DPDP data-rights request logging.

Fulfillment stays manual (see POST /admin/data-rights/{id}/fulfill in
admin.py) -- this router only creates the auditable request record itself,
which is genuine/real, not a stub, even though fulfillment isn't automated.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from backend.models.db import get_db
from backend.models import models as m

router = APIRouter(prefix="/users", tags=["users"])


def _create_request(user_id: str, request_type: str, db: DBSession):
    user = db.query(m.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    req = m.DataRightsRequest(user_id=user_id, request_type=request_type, status="pending_manual_review")
    db.add(req)
    db.commit()
    db.refresh(req)
    return {
        "ok": True, "request_id": req.id, "request_type": req.request_type, "status": req.status,
        "created_at": req.created_at.isoformat(),
        "note": "Request logged. Fulfillment is a manual step handled by the admin team (see /admin/data-rights).",
    }


@router.post("/{user_id}/data-rights/access")
def request_data_access(user_id: str, db: DBSession = Depends(get_db)):
    return _create_request(user_id, "access", db)


@router.post("/{user_id}/data-rights/deletion")
def request_data_deletion(user_id: str, db: DBSession = Depends(get_db)):
    return _create_request(user_id, "deletion", db)
