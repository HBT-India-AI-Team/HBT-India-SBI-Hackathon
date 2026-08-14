"""
SQLAlchemy engine/session management for YONO 3.0.

SQLite file lives at /backend/data/yono.db (path resolved relative to this
file so it works regardless of CWD the app is launched from).
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "yono.db")

DATABASE_URL = os.environ.get("YONO_DATABASE_URL", f"sqlite:///{DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

if DATABASE_URL.startswith("sqlite"):
    # NOTE: this sandbox's mounted filesystem (a FUSE-backed mount, not a
    # native disk) returns "disk I/O error" from SQLite's default rollback
    # journal mode, which relies on filesystem features (fsync/locking
    # semantics) the mount doesn't support. journal_mode=MEMORY +
    # synchronous=OFF avoids touching those unsupported filesystem
    # primitives. This is a sandbox-environment workaround, not a
    # production recommendation -- on a normal disk the default WAL/DELETE
    # journal mode is preferred for durability.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=MEMORY")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """Context manager for scripts/background jobs (non-FastAPI callers)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
