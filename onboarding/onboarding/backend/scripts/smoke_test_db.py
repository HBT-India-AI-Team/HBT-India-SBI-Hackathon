"""
Smoke test proving the models + relationships work end to end: creates a
User, an Application, two Requirements, a Session, and a Message, commits,
reads them all back, prints them.

Run with: python -m backend.scripts.smoke_test_db
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.models.db import Base, engine, SessionLocal
from backend.models import models as m


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = m.User(mobile_number="9999999999", language="en")
        db.add(user)
        db.flush()

        app = m.Application(user_id=user.id, product_id="savings_account", channel_origin="web")
        db.add(app)
        db.flush()

        req1 = m.Requirement(
            application_id=app.id, type="mobile_otp", label="Verify mobile number",
            format_hint="10-digit mobile number", mapped_step=1,
        )
        req2 = m.Requirement(
            application_id=app.id, type="pan", label="PAN verification",
            format_hint="ABCDE1234F", mapped_step=2,
        )
        db.add_all([req1, req2])
        db.flush()

        sess = m.Session(application_id=app.id, channel="web")
        db.add(sess)
        db.flush()

        msg = m.Message(
            session_id=sess.id, direction="outbound", content_type="text",
            content_payload={"text": "Welcome to YONO 3.0! Let's verify your mobile number."},
        )
        db.add(msg)
        db.commit()

        print("=== SMOKE TEST: written ===")
        print("User:", user.id, user.mobile_number)
        print("Application:", app.id, app.product_id, app.status)
        print("Requirements:", [(r.id, r.type, r.state) for r in [req1, req2]])
        print("Session:", sess.id, sess.channel)
        print("Message:", msg.id, msg.content_payload)

        db.expire_all()
        print("\n=== SMOKE TEST: read back ===")
        u2 = db.query(m.User).filter_by(id=user.id).one()
        a2 = db.query(m.Application).filter_by(id=app.id).one()
        print("User readback:", u2.id, u2.mobile_number, u2.language)
        print("Application readback:", a2.id, a2.product_id, a2.status, "user:", a2.user.mobile_number)
        print("Requirements readback:", [(r.type, r.state) for r in a2.requirements])
        print("Sessions readback:", [(s.id, s.channel) for s in a2.sessions])
        for s in a2.sessions:
            print("  Messages:", [(mm.direction, mm.content_payload) for mm in s.messages])
        print("\nSMOKE TEST PASSED")
    finally:
        db.close()


if __name__ == "__main__":
    main()
