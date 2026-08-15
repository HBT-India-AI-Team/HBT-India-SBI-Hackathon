import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.db import Base
from backend.models import models as m


@pytest.fixture()
def db():
    # in-memory sqlite per test, isolated + fast, no mounted-fs quirks
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def make_application(db, product_id, mobile="9876500001"):
    from backend.services import requirement_graph
    user = m.User(mobile_number=mobile, language="en")
    db.add(user)
    db.flush()
    app = m.Application(user_id=user.id, product_id=product_id, channel_origin="web")
    db.add(app)
    db.flush()
    requirement_graph.instantiate_requirements(app, db)
    db.commit()
    db.refresh(app)
    return app
