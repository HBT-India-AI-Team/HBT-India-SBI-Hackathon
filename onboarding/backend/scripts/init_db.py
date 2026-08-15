"""
Creates all tables from the models. No migration framework -- fine for a
hackathon prototype (per Phase 1 spec). Run with:
    python -m backend.scripts.init_db
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.models.db import Base, engine, DB_PATH
from backend.models import models  # noqa: F401  (registers all models on Base)


def main():
    Base.metadata.create_all(bind=engine)
    print(f"[init_db] Tables created. SQLite DB at: {DB_PATH}")
    print("[init_db] Tables:", sorted(Base.metadata.tables.keys()))


if __name__ == "__main__":
    main()
