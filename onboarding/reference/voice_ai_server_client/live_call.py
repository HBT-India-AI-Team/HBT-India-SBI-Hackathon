#!/usr/bin/env python3
"""
Live voice call client: streams your laptop's microphone to the YONO voice
server's WS /call endpoint in real time, and plays back synthesized replies
as they arrive. The server runs VAD, so just talk naturally -- there's no
push-to-talk.

Usage:
    python live_call.py
    python live_call.py --input-device 2 --output-device 1   # see --list-devices

Press Ctrl+C to end the call.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import queue
import sys

import numpy as np
import sounddevice as sd
import websockets

from client_config import load_config


def make_mic_queue(sample_rate: int, frame_ms: int, device: int | None) -> tuple[queue.Queue, sd.InputStream]:
    frame_samples = int(sample_rate * frame_ms / 1000)
    q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        q.put(bytes(indata))

    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
        device=device,
        callback=callback,
    )
    return q, stream


async def sender(ws, mic_queue: queue.Queue, stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        try:
            chunk = await loop.run_in_executor(None, mic_queue.get, True, 0.5)
        except queue.Empty:
            continue
        await ws.send(chunk)


async def receiver(ws, stop_event: asyncio.Event, output_device: int | None) -> None:
    reply_chunks: list[bytes] = []
    reply_sample_rate: int | None = None

    async for message in ws:
        if isinstance(message, bytes):
            reply_chunks.append(message)
            continue

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue

        mtype = payload.get("type")
        if mtype == "ready":
            print(
                f"[call] connected -- talk naturally, server VAD detects your turns "
                f"(in {payload['input_sample_rate']}Hz / out {payload['output_sample_rate']}Hz)"
            )
        elif mtype == "transcript":
            print(f"you:       {payload['text']}  ({payload['latency_ms']:.0f}ms)")
        elif mtype == "reply_text":
            print(f"assistant: {payload['text']}")
        elif mtype == "reply_audio_start":
            reply_chunks = []
            reply_sample_rate = payload["sampling_rate"]
        elif mtype == "reply_audio_end":
            audio_bytes = b"".join(reply_chunks)
            if audio_bytes and reply_sample_rate:
                audio = np.frombuffer(audio_bytes, dtype=np.int16)
                sd.play(audio, samplerate=reply_sample_rate, device=output_device)
                sd.wait()
        elif mtype == "latency":
            print(
                f"           (stt={payload['stt_ms']:.0f}ms "
                f"tts_first_chunk={payload['tts_first_chunk_ms']:.0f}ms "
                f"round_trip={payload['round_trip_ms']:.0f}ms)"
            )
        elif mtype == "call_ended":
            print("[call] server ended the call")
            stop_event.set()
            break
        else:
            print(f"[call] <<< {payload}")


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config()
    mic_queue, stream = make_mic_queue(cfg.sample_rate, cfg.frame_ms, args.input_device)
    stop_event = asyncio.Event()

    print(f"[call] connecting to {cfg.server_url} ...")
    async with websockets.connect(cfg.ws_call_url, max_size=None) as ws:
        with stream:
            print("[call] mic live. Press Ctrl+C to end the call.")
            await asyncio.gather(
                sender(ws, mic_queue, stop_event),
                receiver(ws, stop_event, args.output_device),
            )
    print("[call] call ended")


def list_devices() -> None:
    print(sd.query_devices())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-device", type=int, default=None, help="mic device index (see --list-devices)")
    parser.add_argument("--output-device", type=int, default=None, help="speaker device index (see --list-devices)")
    parser.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[call] ending call ...")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\n[call] connection closed: {e}")
    except websockets.exceptions.InvalidStatus as e:
        print(f"\n[call] connection rejected: {e} -- check the token/URL in client/.env")


if __name__ == "__main__":
    main()
