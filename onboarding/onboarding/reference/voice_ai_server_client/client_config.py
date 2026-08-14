"""
Shared config loader for the laptop-side client scripts.

Reads client/.env (a tiny hand-rolled parser -- no python-dotenv dependency
needed) with a fallback to real environment variables, so YONO_SERVER_URL /
YONO_SERVER_API_KEY can also be exported in the shell if you prefer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


_ENV_FILE = _load_env_file(CLIENT_DIR / ".env")


def _get(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, _ENV_FILE.get(key, default))


@dataclass
class ClientConfig:
    server_url: str
    api_key: str
    sample_rate: int
    frame_ms: int

    @property
    def http_base(self) -> str:
        return self.server_url.rstrip("/")

    @property
    def ws_call_url(self) -> str:
        ws = self.http_base.replace("https://", "wss://").replace("http://", "ws://")
        return f"{ws}/call?token={self.api_key}"

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


def load_config() -> ClientConfig:
    server_url = _get("YONO_SERVER_URL")
    api_key = _get("YONO_SERVER_API_KEY")
    if not server_url:
        raise SystemExit(
            "Set YONO_SERVER_URL in client/.env, e.g. YONO_SERVER_URL=http://192.168.1.42:8000 "
            "(the LAN base URL the server printed on startup)."
        )
    if not api_key:
        raise SystemExit(
            "Set YONO_SERVER_API_KEY in client/.env -- copy it from the server's .env "
            "(YONO_SERVER_API_KEY=... in voice_ai_server/.env)."
        )
    return ClientConfig(
        server_url=server_url,
        api_key=api_key,
        sample_rate=int(_get("YONO_CLIENT_SAMPLE_RATE", "16000")),
        frame_ms=int(_get("YONO_CLIENT_FRAME_MS", "30")),
    )
