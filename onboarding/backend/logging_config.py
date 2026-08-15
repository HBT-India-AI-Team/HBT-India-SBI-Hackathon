"""
Central logging setup for YONO 3.0 backend.

Replaces the old bare `logging.basicConfig(level=logging.INFO)` call that
used to live in main.py. Configures:
  - a console handler (stdout)
  - a rotating file handler writing to backend/data/logs/app.log
    (5MB per file, 5 backups kept)
  - a shared format: timestamp, level, logger name, message
  - root level controlled by the LOG_LEVEL env var (default INFO; set to
    DEBUG to also see per-request bodies logged by the request-logging
    middleware in main.py, and other verbose diagnostic lines)

Call `setup_logging()` once, as early as possible (main.py does this
before importing/constructing the FastAPI app's routers where practical).
"""
import logging
import logging.handlers
import os

from backend import config

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging():
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # avoid duplicate handlers if setup_logging() is somehow called twice
    # (e.g. under --reload) across separate module reload paths
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    log_file = os.path.join(config.LOG_DIR, "app.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # quiet down noisy third-party loggers unless we're explicitly at DEBUG
    if level > logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.getLogger("yono.logging").info(
        "[logging] configured -- level=%s file=%s", config.LOG_LEVEL, log_file
    )
