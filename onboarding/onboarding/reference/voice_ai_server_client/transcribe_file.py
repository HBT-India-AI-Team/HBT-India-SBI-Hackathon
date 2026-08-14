#!/usr/bin/env python3
"""
Upload a local audio file to the server's /transcribe endpoint and print the
transcript. Run from the laptop.

Usage:
    python transcribe_file.py --file sample.wav
    python transcribe_file.py --file sample.wav --language ta
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from client_config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="path to a local audio file (wav/mp3/m4a/...)")
    parser.add_argument("--language", default=None, help="language code, e.g. ta (default: server default)")
    args = parser.parse_args()

    cfg = load_config()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"file not found: {path}")

    with path.open("rb") as f:
        files = {"file": (path.name, f, "application/octet-stream")}
        data = {"language": args.language} if args.language else {}
        print(f"[transcribe] uploading {path} to {cfg.http_base}/transcribe ...")
        resp = requests.post(
            f"{cfg.http_base}/transcribe",
            headers=cfg.auth_header,
            files=files,
            data=data,
            timeout=120,
        )

    if resp.status_code == 401:
        raise SystemExit(f"401 unauthorized: {resp.json().get('detail')} -- check YONO_SERVER_API_KEY in client/.env")
    if resp.status_code == 503:
        raise SystemExit(f"503 server not ready: {resp.json().get('detail')} -- try again shortly")
    if resp.status_code != 200:
        print(f"error {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    result = resp.json()
    print(f"language:   {result['language']}")
    print(f"latency:    {result['latency_ms']:.0f}ms")
    print(f"transcript: {result['text']}")


if __name__ == "__main__":
    main()
