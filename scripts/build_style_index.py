"""Build the vernacular style-example index from the scraped corpus.

FinGuru's Hindi is correct but reads like a circular. The register fix that
scales past a hand-written substitution table is showing the model real
passages of a person explaining money in the language, and letting it match
that voice. This builds the index those passages are retrieved from.

Three decisions worth knowing about:

**Ollama, not sentence-transformers.** The upstream vernacular_style project
uses sentence-transformers + Chroma, which pulls torch -- about 2 GB -- into
a service that today has no ML dependency at all and speaks to Ollama over
HTTP. Embedding through the same Ollama host keeps that property and means
one less runtime to keep alive during a demo. The index is a plain JSON file
of vectors, exactly like doc_index.json.

**bge-m3, not nomic-embed-text.** Measured on this stack, nomic is
English-first and collapses on Indic script (Tamil 0.470 against bge-m3's
0.639), while bge-m3 loses badly on romanized Hinglish (0.438 against 0.618).
Neither wins everywhere, which is why the document index stays on nomic --
its queries arrive translated into English. This index is different: the
corpus is Devanagari and so are the queries, so it is the pure-Indic case
where bge-m3 measured 8/10 at rank 1 against nomic's 0/10. Separate index,
separate model, no regression to the romanized path.

**Style, not content.** These passages are never quoted and never cited.
They shape wording only -- every fact still comes from a tool call. Passages
carrying financial claims are deliberately dropped in `looks_like_advice`
below, because a retrieved example is not verified and must not be able to
smuggle a claim into an answer.

Usage:
    OLLAMA_HOST=... python scripts/build_style_index.py \
        --corpus "C:/path/to/vernacular_style/data/clean" --lang hi
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = Path(os.environ.get(
    "STYLE_INDEX_PATH", REPO_ROOT / "capabilities_impl" / "fixtures" / "style_index.json"))
CACHE_PATH = OUTPUT_PATH.with_suffix(".embedcache.json")
EMBED_MODEL = os.environ.get("STYLE_EMBED_MODEL", "bge-m3")

# Short passages carry no style signal -- "sir bahut badhiya" tells the model
# nothing about how to explain a rate. Long ones stop being one voice.
MIN_CHARS = 80
MAX_CHARS = 700

# A retrieved example is unverified text. Anything that reads like a claim
# about money is dropped rather than risk it being echoed as fact.
_CLAIM = re.compile(
    r"\d+\s*(?:%|फीसदी|प्रतिशत)"          # a rate
    r"|₹\s*\d|\bरु\.?\s*\d|\d+\s*(?:लाख|करोड़|हज़ार|हजार)"   # an amount
    r"|\bसेक्शन\s*\d|\b80\s*[सीcC]\b",     # a tax section
)

# Comment-shaped noise: greetings, tags, pure praise, pure complaint.
_NOISE = re.compile(
    r"^\s*(?:@\w+|sir|sirji|सर|जी|भाई)\s*$"
    r"|^\s*(?:thanks?|thank you|धन्यवाद|शुक्रिया)\b",
    re.IGNORECASE,
)

# Comment sections are full of loan touts. One got through an earlier version
# of this filter -- "kis kis ko lone chaiye ... mai ek log ka no dungi vo 100%
# dilate hai" -- which is a scam solicitation, and putting it in a prompt as
# "write like this" is the worst outcome this file can produce.
_SOLICITATION = re.compile(
    r"\b\d{10}\b|\+91[\s-]?\d"                       # a phone number
    r"|\b(?:whats?app|व्हाट्सएप|वाट्सएप|telegram|dm|inbox)\b"
    r"|100\s*%|१००\s*%|💯"                            # guaranteed-outcome claims
    r"|\b(?:dila|dilwa|dilate|karwa)\w*\b"           # "I'll get it done for you"
    r"|\b(?:agent|एजेंट|कमीशन|commission)\b"
    r"|\b(?:contact|संपर्क|call me|कॉल कर)\w*\b"
    # App pitches. Creators alternate between explaining and selling, and the
    # pitch is fluent colloquial Hindi, so nothing generic catches it. One got
    # through both this filter and the upstream promo filter: "तो देखो भाई मैं
    # ना आपको एक दूसरी मस्त सी एप्लीकेशन बता देता हूं". Anchoring on that
    # teaches the model to pitch a loan app, which is the one voice a bank's
    # assistant must never have.
    r"|(?:एप्लीकेशन|एप्लिकेशन|ऐप|अप्प|app)\s*(?:बता|दिखा|यूज़|यूज|डाउनलोड|इंस्टॉल|ट्राई)"
    r"|(?:डाउनलोड|इंस्टॉल|download|install)\s*(?:कर|कीजिए|करना|करिए|link|लिंक)"
    r"|(?:लिंक|link)\s*(?:नीचे|डिस्क्रिप्शन|description|dscr)"
    r"|(?:सब्सक्राइब|subscribe|चैनल को)\b",
    re.IGNORECASE,
)

# Transcript boilerplate: the presenter introducing themselves or the show.
# Fluent, on-topic-adjacent, and useless as style -- anchoring on "नमस्कार, मैं
# हूं आपके साथ संध्या बिष्ट और आप देख रहे हैं द इकोनॉमिक टाइम्स" teaches the
# model to open by introducing itself as a TV presenter. A comment corpus had
# no equivalent; this is new with transcripts.
_PRESENTER = re.compile(
    r"मैं\s*हूँ?ं?\s*आपके\s*साथ"
    r"|आप\s*देख\s*रहे\s*ह[ैे]ं"
    r"|(?:नमस्कार|नमस्ते|हैलो|स्वागत)\b.{0,40}(?:मैं|आपका|आप सभी|दोस्तों)"
    r"|मेरा\s*नाम\s*\S+\s*ह[ैू]"
    r"|(?:इस\s*वीडियो\s*में|आज\s*के\s*वीडियो|वीडियो\s*में\s*हम)"
    r"|(?:एपिसोड|शो\s*में|कार्यक्रम\s*में)",
)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Screen narration. A large slice of Hindi finance YouTube is someone walking
# through an app -- "tap here, enter the OTP, upload a selfie" -- which is
# fluent, colloquial, on-topic-adjacent, and the wrong thing entirely: it
# teaches an assistant that cannot see a screen to narrate one. Measured at
# 22% of the first index built from this corpus.
#
# Two markers required, not one. A single "क्लिक कर" inside a real
# explanation of how to open an account online is legitimate; a passage built
# out of them is a tutorial.
_UI_MARKER = re.compile(
    r"स्लाइडर|कैप्चा|ओटीपी|\bOTP\b|टैप कर|क्लिक कर|स्क्रीन|इंटरफेस"
    r"|ऑप्शन पे|बटन|सेल्फी|अपलोड|लॉग ?इन|डैशबोर्ड|पेज पे|यहां पे लिखा"
    r"|दिख रहा है|नीचे स्क्रॉल|मेनू में",
)


def looks_like_advice(text: str) -> tuple[bool, str]:
    """Keep passages that explain; drop questions, claims and noise.

    The corpus this was first pointed at is YouTube *comments*, which are
    overwhelmingly the audience asking rather than anyone explaining -- the
    wrong register to imitate, and in places actively hostile to banks. This
    filter is what makes a comment corpus survivable and a transcript corpus
    clean; it is deliberately strict, because a bad exemplar is worse than a
    missing one.
    """
    stripped = text.strip()
    if not (MIN_CHARS <= len(stripped) <= MAX_CHARS):
        return False, "length"
    if _NOISE.search(stripped):
        return False, "noise"
    if _SOLICITATION.search(stripped):
        return False, "solicitation"
    if _PRESENTER.search(stripped):
        return False, "presenter boilerplate"
    if len(_UI_MARKER.findall(stripped)) >= 2:
        return False, "app screen narration"
    if _CLAIM.search(stripped):
        return False, "carries a figure"

    # Retrieval routes on script: the index is keyed by language and only
    # Devanagari queries reach it, so a romanized passage can be embedded but
    # never matched. Requiring the script here keeps the index honest about
    # what it can actually serve.
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False, "no letters"
    if sum(1 for c in letters if _DEVANAGARI.match(c)) / len(letters) < 0.6:
        return False, "not Devanagari"
    # A passage that is mostly a question is the audience's voice, not an
    # explainer's. One trailing question ("समझ आया?") is fine.
    if stripped.count("?") + stripped.count("？") > 1:
        return False, "question"
    if stripped.rstrip().endswith(("?", "？")) and len(stripped) < 200:
        return False, "question"
    return True, ""


def _cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def embed(text: str, cache: dict, host: str) -> list[float] | None:
    """Embed with retry and a persistent cache.

    The cache is keyed on model+text so re-running after adding a language
    re-embeds only what is new, and a tunnel that drops mid-build resumes
    instead of starting over -- which it did, three times, while building the
    document index.
    """
    key = hashlib.sha256(f"{EMBED_MODEL}\x00{text}".encode()).hexdigest()
    if key in cache:
        return cache[key]

    body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    for attempt, delay in enumerate((5, 15, 45, 135), start=1):
        request = urllib.request.Request(
            host.rstrip("/") + "/api/embeddings", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                vector = json.load(response).get("embedding")
            if vector:
                cache[key] = vector
                return vector
        except Exception as exc:            # noqa: BLE001 - any failure retries
            print(f"    embed attempt {attempt} failed ({exc}); retrying in {delay}s",
                  file=sys.stderr)
            time.sleep(delay)
    return None


def load_corpus(corpus_dir: Path, stems: list[str]) -> list[dict]:
    """`stems` are file names, not language codes.

    The transcript corpus lives in hi_transcript.jsonl while its records are
    tagged `language: hi`, so taking the language from the file name would
    key the index "hi_transcript" -- which `style_examples.language_of` never
    returns, leaving retrieval silently matching nothing. The record's own
    field wins; the stem is only a fallback for older files without one.
    """
    rows: list[dict] = []
    for stem in stems:
        path = corpus_dir / f"{stem}.jsonl"
        if not path.exists():
            print(f"  {stem}: no {path.name}, skipping")
            continue
        kept, dropped = 0, {}
        languages: dict[str, int] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Hinglish rows are tagged script: latin upstream and excluded
            # there unless asked for. Honour the tag rather than relying on
            # the Devanagari check below to catch them by accident.
            if record.get("script") == "latin":
                dropped["romanized (tagged)"] = dropped.get("romanized (tagged)", 0) + 1
                continue

            text = (record.get("text") or "").strip()
            ok, why = looks_like_advice(text)
            if not ok:
                dropped[why] = dropped.get(why, 0) + 1
                continue
            language = record.get("language") or stem
            languages[language] = languages.get(language, 0) + 1
            rows.append({
                "text": text,
                "language": language,
                "source": record.get("source", "unknown"),
                "video_id": record.get("video_id"),
            })
            kept += 1
        detail = ", ".join(f"{k} {v}" for k, v in sorted(dropped.items()))
        tagged = ", ".join(f"{k}={v}" for k, v in sorted(languages.items()))
        print(f"  {stem}: kept {kept} [{tagged or 'none'}]  (dropped: {detail or 'none'})")
    return rows


def merge_adjacent(rows: list[dict], max_chars: int = MAX_CHARS) -> list[dict]:
    """Rejoin consecutive chunks from the same video into one passage.

    Transcripts are chunked at 2-3 sentences, so an exemplar is a fragment
    from the middle of an explanation with no beginning and no end. Shown
    three of those, the model matches the fragment shape and answers in
    flowing pieces -- which is where the measured 10% shortening and the
    dropped trailing sections come from.

    Merging runs after the per-chunk filter rather than before it, so a
    figure or a promo line still removes only its own chunk instead of
    poisoning its neighbours.
    """
    by_video: dict[str, list[dict]] = {}
    for row in rows:
        by_video.setdefault(row.get("video_id") or "?", []).append(row)

    merged: list[dict] = []
    for video, chunks in by_video.items():
        chunks.sort(key=lambda r: r.get("chunk_index") or 0)
        buffer: list[dict] = []

        def flush() -> None:
            if not buffer:
                return
            head = buffer[0]
            merged.append({**head, "text": " ".join(c["text"] for c in buffer)})
            buffer.clear()

        for chunk in chunks:
            index = chunk.get("chunk_index")
            contiguous = (
                buffer
                and isinstance(index, int)
                and isinstance(buffer[-1].get("chunk_index"), int)
                and index == buffer[-1]["chunk_index"] + 1
            )
            room = sum(len(c["text"]) + 1 for c in buffer) + len(chunk["text"]) <= max_chars
            if contiguous and room:
                buffer.append(chunk)
            else:
                flush()
                buffer.append(chunk)
        flush()
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True,
                        help="directory holding <lang>.jsonl style passages")
    parser.add_argument("--lang", action="append", dest="languages", default=None,
                        help="language code; repeatable (default: hi)")
    parser.add_argument("--merge-adjacent", action="store_true",
                        help="rejoin consecutive chunks from one video into fuller passages")
    args = parser.parse_args()

    languages = args.languages or ["hi"]
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_dir():
        print(f"No such corpus directory: {corpus_dir}", file=sys.stderr)
        return 1

    print(f"Reading {corpus_dir} for {', '.join(languages)}")
    rows = load_corpus(corpus_dir, languages)
    if args.merge_adjacent and rows:
        before = len(rows)
        rows = merge_adjacent(rows)
        lengths = sorted(len(r["text"]) for r in rows)
        print(f"  merged {before} chunks -> {len(rows)} passages "
              f"(median {lengths[len(lengths) // 2]} chars)")
    if not rows:
        print("Nothing survived filtering -- index NOT written.", file=sys.stderr)
        print("If the corpus is YouTube comments this is expected: they are the "
              "audience asking, not an advisor explaining. Point --corpus at "
              "transcript passages instead.", file=sys.stderr)
        return 1

    cache = _cache()
    print(f"Embedding {len(rows)} passages with {EMBED_MODEL}")
    embedded = []
    try:
        for i, row in enumerate(rows, start=1):
            vector = embed(row["text"], cache, host)
            if vector is None:
                print(f"  giving up on passage {i}", file=sys.stderr)
                continue
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            embedded.append({**row, "embedding": [v / norm for v in vector]})
            if i % 25 == 0:
                print(f"  {i}/{len(rows)}")
    finally:
        # Written even on interrupt so a long build is never lost.
        CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")

    if not embedded:
        print("No passage embedded -- index NOT written.", file=sys.stderr)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Written only at the end: a mid-build crash leaves the live index intact.
    OUTPUT_PATH.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "embedding_model": EMBED_MODEL,
        "embedding_dimensions": len(embedded[0]["embedding"]),
        "languages": sorted({r["language"] for r in embedded}),
        "passages": embedded,
    }, ensure_ascii=False), encoding="utf-8")

    by_lang: dict[str, int] = {}
    for row in embedded:
        by_lang[row["language"]] = by_lang.get(row["language"], 0) + 1
    print(f"\nWrote {OUTPUT_PATH} — {len(embedded)} passages "
          f"({', '.join(f'{k} {v}' for k, v in sorted(by_lang.items()))})")
    print("Restart the backend: the index is cached in module memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
