#!/usr/bin/env python3
"""
Send text to the server's /synthesize endpoint, save the resulting WAV, and
optionally play it through the laptop's speakers. Run from the laptop.

Usage:
    python synthesize_text.py --text "வணக்கம், உங்கள் கணக்கு தயார்." --play
    python synthesize_text.py --text "hello" --language ta --output out.wav
"""
from __future__ import annotations

import argparse
import sys
import wave

import requests

from client_config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", required=True)
    parser.add_argument("--language", default="ta")
    parser.add_argument("--speaker-style", default=None, help="natural-language voice description override")
    parser.add_argument("--output", default="synthesized.wav")
    parser.add_argument("--play", action="store_true", help="play the audio through speakers after saving")
    args = parser.parse_args()

    cfg = load_config()
    body = {"text": args.text, "language": args.language}
    if args.speaker_style:
        body["speaker_style"] = args.speaker_style

    print(f"[synthesize] requesting audio from {cfg.http_base}/synthesize ...")
    resp = requests.post(
        f"{cfg.http_base}/synthesize",
        headers=cfg.auth_header,
        json=body,
        timeout=120,
    )

    if resp.status_code == 401:
        raise SystemExit(f"401 unauthorized: {resp.json().get('detail')} -- check YONO_SERVER_API_KEY in client/.env")
    if resp.status_code == 503:
        raise SystemExit(f"503 server not ready: {resp.json().get('detail')} -- try again shortly")
    if resp.status_code != 200:
        print(f"error {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "wb") as f:
        f.write(resp.content)
    latency = resp.headers.get("X-Latency-Ms", "?")
    print(f"saved -> {args.output} ({len(resp.content)} bytes, {latency}ms)")

    if args.play:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            print("install sounddevice+numpy to use --play: pip install -r requirements.txt", file=sys.stderr)
            return
        with wave.open(args.output, "rb") as wf:
            samplerate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16)
        print("playing ...")
        sd.play(audio, samplerate=samplerate)
        sd.wait()


if __name__ == "__main__":
    main()
