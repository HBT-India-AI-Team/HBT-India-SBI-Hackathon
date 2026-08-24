"""Emit each sentence the moment it is finished, not when the answer is.

A grounded reply here runs 10-25 seconds. A client that waits for all of it
before speaking any of it leaves the listener in silence for the whole of
that, then delivers a monologue. Handing over sentence by sentence turns the
wait into time-to-first-word, which is a fraction of it.

**We produce text and nothing else.** Speech synthesis, playback and the
audio channel all belong to the client; this side never calls a TTS service.
An earlier version did, which was wrong for this architecture: the browser
owns the speaker, so a WAV generated here would have had nowhere to go.

## Shape

    Ollama stream ──(thread)──► asyncio.Queue ──► extract ──► split
                                                                │
                                        one task per sentence ──┘
                                                                ▼
                                                        sink (SSE, socket…)

`requests` has no async form and nothing async is installed, so the read runs
in a worker thread via `asyncio.to_thread` and feeds a queue. Each finished
sentence is dispatched with `create_task`, so a slow consumer never stalls
token consumption — which is the whole point of doing this at all. Tasks are
awaited once at the end.

## What it refuses to do

**It does not fail the answer.** Every sink error is caught and counted. A
reply that arrived whole at the end but not sentence-by-sentence is a
degraded success; an exception here would turn it into nothing at all.

**It does not retry a broken stream.** By the time a stream dies, part of it
has already gone out and may already have been spoken. Replaying from the top
would repeat it. The caller falls back to the non-streaming path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_platform.llm.streaming import JsonStringField, SentenceSplitter
from agent_platform.llm.tts_text import clean_for_speech

logger = logging.getLogger(__name__)

# The sink a run should send its sentences to, set by whoever is streaming.
#
# A ContextVar rather than an argument because the sink is decided at the HTTP
# edge and consumed six frames down, inside a pipeline stage whose signature
# is shared with every other agent. Threading a callable through all of that
# would put a speech concern into code that has nothing to do with speech.
#
# It does not cross a thread boundary by itself -- a worker must be started
# with contextvars.copy_context().run(...) for the value to follow it, which
# is what backend/main.py does.
sentence_sink: ContextVar[Callable[[str, str | None], None] | None] = ContextVar(
    "sentence_sink", default=None,
)

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
    # Set when `script_ok` rejected the opening sentence and speech was held
    # back for the whole turn. The answer itself is unaffected -- it is still
    # generated, parsed and returned; only the audio is withheld, so the
    # caller's own correction pass can fix the text before anything is heard.
    script_suppressed: bool = False

    @property
    def first_sentence_ms(self) -> float | None:
        """Time to first spoken word — the number this design exists to move."""
        return self.sentences[0].elapsed_ms if self.sentences else None


async def _forward(event: SentenceEvent, language: str | None,
                   sink: Callable[[str, str | None], None] | None) -> None:
    """Hand one sentence to the consumer, off the token-reading path.

    Every failure is swallowed into the event. The client losing one sentence
    early is bad; losing the whole answer because a consumer misbehaved is
    worse -- the full text is still returned at the end either way.
    """
    send = sink or sentence_sink.get()
    if send is None:
        # No one is listening. Splitting and timing still ran, which is what
        # every non-streaming caller sees.
        event.error = "no sink"
        return
    try:
        await asyncio.to_thread(send, event.text, language)
        event.forwarded = True
    except Exception as exc:                    # noqa: BLE001 - never fatal
        event.error = f"{type(exc).__name__}: {exc}"
        logger.warning("Sentence %d not delivered: %s", event.index, event.error)


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
    normalize: bool = False,
    script_ok: Callable[[str], bool] | None = None,
) -> StreamResult:
    """Stream the answer, speaking each sentence as it completes.

    `sink` overrides the HTTP forwarder — used by tests, and by any caller
    that would rather push over an already-open WebSocket than open a
    connection per sentence.

    `normalize` strips markdown, list markers and brackets from each sentence
    before it goes out. Off by default and switched on only in voice mode:
    this same stream feeds the on-screen provisional bubble when voice is off,
    and stripping `**bold**` out of *that* would be damage, not cleanup.

    `script_ok(sentence) -> bool` vets the FIRST sentence only. Returning False
    holds back speech for the whole turn. This exists because streaming and
    correction pull in opposite directions: the caller can detect a reply
    written in the wrong script and regenerate it, but by then a stream has
    already spoken the wrong one, leaving corrected text on screen beside
    audio that said something else. Withholding audio the moment the opening
    sentence looks wrong keeps the two in agreement -- the caller repairs the
    text, and the client speaks the repaired version instead. Only the first
    sentence is judged: a reply does not change script halfway, and re-judging
    every sentence would risk cutting a good answer off mid-flow.

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
        if result.script_suppressed:
            return                              # opening sentence was rejected
        if normalize:
            # Cleaned here, at the single point a sentence is finalized, so
            # every path out -- SSE, socket, test sink -- gets the same text.
            # Only what is *spoken* is cleaned: result.raw and the parsed
            # `content` keep what the model actually wrote, which is what
            # makes the leak visible in logs instead of silently papered over.
            text = clean_for_speech(text)
            if not text:
                return                          # nothing left but markup
        if script_ok is not None and not result.sentences and not script_ok(text):
            result.script_suppressed = True
            logger.warning(
                "opening sentence failed the script check -- holding back speech "
                "for this turn so the caller can correct the text first: %s",
                text[:80],
            )
            return
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
