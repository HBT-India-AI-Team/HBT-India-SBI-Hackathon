"""Append new style-corpus passages to the existing style_index.json.

build_style_index.py is all-or-nothing: point it at a corpus directory and it
REWRITES the whole index from only what's in that directory. That's the
wrong tool for "I found one more transcript file" -- this script instead
embeds only the NEW rows (through the same filters build_style_index.py
uses) and appends them to the passages already in style_index.json, leaving
everything already indexed untouched and un-re-embedded.

Usage:
    OLLAMA_HOST=https://your-ollama-host python scripts/ingest_style_corpus.py \
        path/to/new_corpus.jsonl [more.jsonl ...]

Each input file is JSON-lines, the same shape build_style_index.py expects --
one passage per line:
    {"text": "...", "language": "hi"|"ta", "source": "...",
     "video_id": "...", "chunk_index": 0}

Rows are deduplicated by a hash of their (filtered) text, so re-running this
against a file already ingested is a safe no-op for those rows.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_style_index as bsi  # noqa: E402


def _passage_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_style_corpus.py <file.jsonl> [more.jsonl ...]",
              file=sys.stderr)
        return 1

    inputs = [Path(p) for p in sys.argv[1:]]
    for p in inputs:
        if not p.exists():
            print(f"No such file: {p}", file=sys.stderr)
            return 1

    if bsi.OUTPUT_PATH.exists():
        index = json.loads(bsi.OUTPUT_PATH.read_text(encoding="utf-8"))
    else:
        index = {"built_at": None, "embedding_model": bsi.EMBED_MODEL,
                  "embedding_dimensions": None, "languages": [], "passages": []}

    existing_keys = {_passage_key(p["text"]) for p in index["passages"]}
    print(f"Existing index: {len(index['passages'])} passages "
          f"({', '.join(index.get('languages', [])) or 'none'})")

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    cache = bsi._cache()

    new_rows: list[dict] = []
    for path in inputs:
        kept, dropped, skipped_dupe = 0, {}, 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("script") == "latin":
                continue
            text = bsi.strip_caption_noise(record.get("text") or "")
            language = record.get("language") or ""
            ok, why = bsi.looks_like_advice(text, language)
            if not ok:
                dropped[why] = dropped.get(why, 0) + 1
                continue
            key = _passage_key(text)
            if key in existing_keys:
                skipped_dupe += 1
                continue
            existing_keys.add(key)
            new_rows.append({
                "text": text, "language": language,
                "source": record.get("source", "unknown"),
                "video_id": record.get("video_id"),
                "chunk_index": record.get("chunk_index"),
            })
            kept += 1
        detail = ", ".join(f"{k} {v}" for k, v in sorted(dropped.items()))
        print(f"  {path.name}: kept {kept}, skipped {skipped_dupe} already-indexed "
              f"(dropped: {detail or 'none'})")

    if not new_rows:
        print("Nothing new to add -- index left unchanged.")
        return 0

    print(f"Embedding {len(new_rows)} new passages with {bsi.EMBED_MODEL}")
    embedded: list[dict] = []
    try:
        for i, row in enumerate(new_rows, start=1):
            vector = bsi.embed(row["text"], cache, host)
            if vector is None:
                print(f"  giving up on passage {i}", file=sys.stderr)
                continue
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            embedded.append({**row, "embedding": [v / norm for v in vector]})
            if i % 25 == 0:
                print(f"  {i}/{len(new_rows)}")
    finally:
        # Written even on interrupt so a long ingest is never lost.
        bsi.CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")

    if not embedded:
        print("No new passage embedded -- index left unchanged.", file=sys.stderr)
        return 1

    index["passages"].extend(embedded)
    index["languages"] = sorted({r["language"] for r in index["passages"]})
    index["embedding_dimensions"] = len(embedded[0]["embedding"])
    index["embedding_model"] = bsi.EMBED_MODEL
    index["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    bsi.OUTPUT_PATH.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    by_lang: dict[str, int] = {}
    for r in index["passages"]:
        by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
    print(f"\nWrote {bsi.OUTPUT_PATH} -- {len(index['passages'])} passages total "
          f"({', '.join(f'{k} {v}' for k, v in sorted(by_lang.items()))})")
    print("Restart the backend: the index is cached in module memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
