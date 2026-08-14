"""Speak each sentence the moment it is finished, not when the answer is.

A grounded reply here runs 10-25 seconds. Waiting for all of it before
speaking any of it means the listener hears silence for the whole of that,
then a monologue. Forwarding sentence by sentence turns the wait into
time-to-first-word, which is a fraction of it.

## Shape

    Ollama stream ──(thread)──► asyncio.Queue ──► extract ──► split
                                                                │
                                        one task per sentence ──┘
                                                                ▼
                                                          speech service

`requests` has no async form and nothing async is installed, so the read runs
in a worker thread via `asyncio.to_thread` and feeds a queue. Each finished
sentence is dispatched with `create_task`, so a slow or unreachable speech
service never stalls token consumption — which is the whole point of doing
this at all. Tasks are awaited once at the end.

## What it refuses to do

**It does not fail the answer.** Every speech-side error is caught and
counted. A reply that reached the user as text but not as audio is a degraded
success; an exception here would turn it into nothing at all.

**It does not retry a broken stream.** By the time a stream dies, part of it
has already been spoken. Replaying from the top would repeat audio the
listener has heard. The caller falls back to the non-streaming path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from agent_platform.llm.streaming import JsonStringField, SentenceSplitter

logger = logging.getLogger(__name__)

# The speech service is on another machine (the GPU box). Unset means "do not
# forward" -- streaming still works and still logs, it just has nowhere to
# send to, which is the right behaviour on a laptop with no GPU peer.
_TTS_URL = os.environ.get("VOICE_TTS_URL", "").strip()
_TTS_TOKEN = os.environ.get("VOICE_TTS_TOKEN", "").strip()
_TTS_TIMEOUT = float(os.environ.get("VOICE_TTS_TIMEOUT_SECONDS", "10"))

# The field of the output contract that holds the words to speak. The rest of
# the object -- language, content_type, confidence -- is machinery.
_SPEECH_FIELD = "content"


@dataclass
class SentenceEvent:
    """One sentence, and how long the listener waited for it."""
    index: int
    text: str
    elapsed_ms: float
    forwarded: bool = False
    error: str | None = None


@dataclass
class StreamResult:
    parsed: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    sentences: list[SentenceEvent] = field(default_factory=list)
    raw: str = ""

    @property
    def first_sentence_ms(self) -> float | None:
        """Time to first spoken word — the number this design exists to move."""
        return self.sentences[0].elapsed_ms if self.sentences else None


def _post_sentence(text: str, language: str | None) -> None:
    """One blocking POST to the speech service. Raises on failure."""
    headers = {"Content-Type": "application/json"}
    if _TTS_TOKEN:
        headers["Authorization"] = f"Bearer {_TTS_TOKEN}"
    payload: dict[str, Any] = {"text": text}
    if language:
        payload["language"] = language
    response = requests.post(_TTS_URL, json=payload, headers=headers, timeout=_TTS_TIMEOUT)
    response.raise_for_status()


async def _forward(event: SentenceEvent, language: str | None,
                   sink: Callable[[str, str | None], None] | None) -> None:
    """Hand one sentence to speech, off the token-reading path.

    Every failure is swallowed into the event. The listener losing one
    sentence of audio is bad; the caller losing the whole answer because a
    speech box was down is worse.
    """
    send = sink or _post_sentence
    if send is _post_sentence and not _TTS_URL:
        return
    try:
        await asyncio.to_thread(send, event.text, language)
        event.forwarded = True
    except Exception as exc:                    # noqa: BLE001 - never fatal
        event.error = f"{type(exc).__name__}: {exc}"
        logger.warning("Sentence %d not spoken: %s", event.index, event.error)


def _drain_to_queue(stream, q: "queue.Queue[Any]") -> None:
    """Run the blocking Ollama read in a thread, pushing deltas to a queue.

    Exceptions travel through the queue rather than being raised here, where
    nothing would be watching for them.
    """
    try:
        for delta, body in stream:
            q.put(("delta", delta, body))
    except Exception as exc:                    # noqa: BLE001 - relayed below
        q.put(("error", exc, None))
    finally:
        q.put(("end", None, None))


async def stream_to_speech(
    adapter,
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    temperature: float = 0.0,
    language: str | None = None,
    sink: Callable[[str, str | None], None] | None = None,
) -> StreamResult:
    """Stream the answer, speaking each sentence as it completes.

    `sink` overrides the HTTP forwarder — used by tests, and by any caller
    that would rather push over an already-open WebSocket than open a
    connection per sentence.

    Returns everything the non-streaming path returns, plus per-sentence
    timings, so the caller can validate the answer exactly as before.
    """
    t0 = time.perf_counter()
    q: "queue.Queue[Any]" = queue.Queue()
    stream = adapter.stream_structured(
        system_prompt=system_prompt, user_prompt=user_prompt,
        schema=schema, temperature=temperature,
    )
    reader = threading.Thread(target=_drain_to_queue, args=(stream, q), daemon=True)
    reader.start()

    extractor = JsonStringField(_SPEECH_FIELD)
    splitter = SentenceSplitter()
    result = StreamResult()
    tasks: list[asyncio.Task] = []
    failure: Exception | None = None
    raw_parts: list[str] = []

    def dispatch(text: str) -> None:
        event = SentenceEvent(index=len(result.sentences) + 1, text=text,
                              elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
        result.sentences.append(event)
        # Per-sentence latency, measured from the start of the stream. The
        # first of these is the number that matters; the rest show whether
        # generation is keeping ahead of speech.
        logger.info("sentence %d at %.0fms (%d chars): %s",
                    event.index, event.elapsed_ms, len(text), text[:60])
        tasks.append(asyncio.create_task(_forward(event, language, sink)))

    while True:
        # Blocking get, off the event loop, so waiting on the model never
        # stops a dispatched sentence from being sent.
        kind, payload, body = await asyncio.to_thread(q.get)
        if kind == "end":
            break
        if kind == "error":
            failure = payload
            break
        if payload:
            raw_parts.append(payload)
            for sentence in splitter.feed(extractor.feed(payload)):
                dispatch(sentence)
        if body is not None:
            result.meta = adapter._call_metadata(body, (time.perf_counter() - t0) * 1000)

    trailing = splitter.flush()
    if trailing:
        dispatch(trailing)

    if tasks:
        await asyncio.gather(*tasks)

    if failure is not None:
        raise failure

    result.raw = "".join(raw_parts)
    try:
        result.parsed = json.loads(result.raw)
    except json.JSONDecodeError:
        # The text was already spoken; only the structured wrapper is
        # unusable. The caller decides whether that is recoverable.
        result.parsed = None
    return result
