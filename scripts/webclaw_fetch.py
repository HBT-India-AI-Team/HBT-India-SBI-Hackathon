"""Thin wrapper around the webclaw CLI, for pulling source pages at build time.

Why this exists at all: every fact FinGuru is allowed to quote comes from a
page someone has to fetch and read -- RBI FAQs into the document index, the
SBI offers page into the offers fixture. Until now that was `urllib` plus a
regex tag-strip, which flattens a table into a run of words. On a rate card or
an offer slab that is exactly the wrong thing to lose: "Above Rs 49,999 Upto
Rs 99,999 | Flat 2500" becomes "...Upto Rs 99,999 Flat 2500 Above Rs 99,999",
and which number belongs to which band is now a guess. webclaw keeps the
table as a table.

WHERE THIS MAY BE CALLED FROM
-----------------------------
Build-time scripts only. Nothing in the request path may import this, for the
same reason build_doc_index.py states in its own docstring: a live fetch
inside a chat turn puts a third-party site's uptime, rate limits and latency
directly in the user's way. webclaw also ships an MCP server and a REST
server; neither belongs behind a chat turn either. The agent reads built
fixtures, never the web.

WHAT IT IS AND IS NOT GOOD FOR
------------------------------
Measured on our own two sources (see `--self-test`), webclaw is a better
*reader* than the regex strip and not a replacement for a human:

  - It preserves genuine HTML tables. The Reliance Digital slab table comes
    back as a real markdown table, one row per band.
  - It does NOT reliably preserve column association in the hand-built
    layout tables SBI uses for the Cleartrip and Flipkart Travel offers. The
    coupon codes and the categories both survive; which code goes with which
    category does not. Read those with your eyes.

So the intended shape is fetch -> diff -> a human approves the change, never
fetch -> write a fixture. A misparsed number that lands in a fixture is
indistinguishable from a correct one at runtime, which is the failure mode
india_rates.py was hand-curated to avoid in the first place.

INSTALL
-------
The binary is not vendored -- it is ~25 MB and this repo is public. Get it
from https://github.com/0xMassi/webclaw/releases and either put it on PATH or
point WEBCLAW_BIN at it. On this machine it lives in
%LOCALAPPDATA%\\webclaw\\webclaw.exe, which is one of the paths searched
below, so no configuration is needed.

No API key is required for anything here. WEBCLAW_API_KEY only buys hosted
JS-rendering and bot-wall bypass, and neither of our sources needs it: both
rbi.org.in and sbi.bank.in return complete server-rendered HTML to a plain
GET. Do not add the key expecting it to fix a parsing problem.

Usage:
    python scripts/webclaw_fetch.py --self-test
    python scripts/webclaw_fetch.py https://example.com/page --format llm
    python scripts/webclaw_fetch.py <url> --json --out page.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Searched in order. WEBCLAW_BIN wins so a teammate on a different layout, or
# anyone testing a new version, can override without editing this file.
_CANDIDATES = (
    os.environ.get("WEBCLAW_BIN"),
    "webclaw",                                              # PATH
    os.path.expandvars(r"%LOCALAPPDATA%\webclaw\webclaw.exe"),
    str(Path.home() / ".local" / "bin" / "webclaw"),
    "/usr/local/bin/webclaw",
)

FORMATS = ("markdown", "llm", "text", "json", "html")


class WebclawMissing(RuntimeError):
    """Raised when no binary could be found. Callers that have a usable
    fallback should catch this; callers that don't should let it surface,
    because a silent fallback to a worse extractor is how a mangled table
    becomes a fixture."""


def binary() -> str | None:
    """Path to a runnable webclaw, or None."""
    for candidate in _CANDIDATES:
        if not candidate:
            continue
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def available() -> bool:
    return binary() is not None


def version() -> str | None:
    exe = binary()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def fetch(url: str, fmt: str = "llm", only_main_content: bool = True,
          timeout: int = 180, extra_args: list[str] | None = None) -> dict[str, Any]:
    """Extract one page. Returns the text plus enough provenance to cite it.

    `fmt="llm"` is the default because it is the token-lean one and still
    keeps tables; use "json" when you want webclaw's parsed metadata (title,
    language, word_count, JSON-LD) alongside the markdown, and "html" when
    something downstream needs to do its own parsing.

    The returned dict always says which extractor produced the text, under
    `via`. Nothing downstream should have to infer whether it is holding
    markdown or raw HTML.
    """
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}, got {fmt!r}")
    exe = binary()
    if not exe:
        raise WebclawMissing(
            "webclaw not found. Install from "
            "https://github.com/0xMassi/webclaw/releases and put it on PATH, "
            "or set WEBCLAW_BIN to the executable."
        )

    argv = [exe, url, "--format", fmt]
    if only_main_content:
        # Drops the nav, the cookie banner and SBI's Bhashini translation
        # disclaimer, which otherwise open every single extraction.
        argv.append("--only-main-content")
    argv += extra_args or []

    # encoding is explicit: without it Python decodes the child's stdout with
    # the Windows ANSI codepage and every rupee sign and Devanagari character
    # in a page comes back mojibake.
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"webclaw exited {proc.returncode} for {url}\n"
            f"{(proc.stderr or '').strip()[:800]}"
        )

    body = proc.stdout
    result: dict[str, Any] = {
        "url": url,
        "format": fmt,
        "via": "webclaw",
        "tool_version": version(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chars": len(body),
    }
    if fmt == "json":
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"webclaw --format json returned unparseable output: {exc}") from exc
        result["data"] = parsed
        result["text"] = (parsed.get("content") or {}).get("markdown", "")
        result["metadata"] = parsed.get("metadata") or {}
    else:
        result["text"] = body
    return result


def fetch_raw_html(url: str, timeout: int = 90) -> dict[str, Any]:
    """The pre-webclaw path, kept so a teammate without the binary is not
    blocked and so `--self-test` has something to compare against.

    This returns raw HTML and says so. It is deliberately not a transparent
    fallback inside `fetch()`: code that asked for markdown and silently got
    HTML would parse it into nonsense rather than fail.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (FinGuru doc indexer)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="ignore")
    return {
        "url": url,
        "format": "html",
        "via": "urllib",
        "tool_version": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chars": len(body),
        "text": body,
    }


# The two pages this repo actually depends on, one of each kind: a plain
# server-rendered FAQ and a table-heavy offers page.
_SELF_TEST_URLS = [
    ("RBI FAQ — Deposit Insurance", "https://rbi.org.in/scripts/FAQView.aspx?Id=64"),
    ("SBI — Debit Card Offers",
     "https://sbi.bank.in/web/personal-banking/cards/debit-card/debit-card-offers"),
]

# Strings that must survive extraction. If a future webclaw release or a site
# redesign drops them, this fails loudly instead of quietly returning a page
# with the numbers missing.
_MUST_SURVIVE = {
    "https://sbi.bank.in/web/personal-banking/cards/debit-card/debit-card-offers":
        ["Flat 2500", "49,999", "Onam", "Offer Period", "Big Basket"],
    "https://rbi.org.in/scripts/FAQView.aspx?Id=64": ["insur", "deposit"],
}


def self_test() -> int:
    """Confirm the install works against our own sources. Returns an exit code."""
    exe = binary()
    print(f"binary      : {exe or 'NOT FOUND'}")
    print(f"version     : {version() or '-'}")
    if not exe:
        print("\nNot installed. See this module's docstring for where to get it.")
        return 1

    failures = 0
    for name, url in _SELF_TEST_URLS:
        print(f"\n{name}\n  {url}")
        try:
            clawed = fetch(url, fmt="llm")
        except Exception as exc:  # noqa: BLE001 — a self-test reports, never raises
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        try:
            plain = fetch_raw_html(url)
            ratio = f"{clawed['chars'] / plain['chars']:.1%} of raw HTML"
        except Exception:  # noqa: BLE001 — the comparison is a nicety
            ratio = "raw HTML unavailable for comparison"

        print(f"  extracted : {clawed['chars']:,} chars ({ratio})")
        missing = [n for n in _MUST_SURVIVE.get(url, [])
                   if n.lower() not in clawed["text"].lower()]
        if missing:
            print(f"  MISSING   : {missing}")
            failures += 1
        else:
            print(f"  kept      : {_MUST_SURVIVE.get(url, [])}")

    print("\n" + ("OK" if not failures else f"{failures} check(s) failed"))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", help="page to extract")
    parser.add_argument("--format", default="llm", choices=FORMATS)
    parser.add_argument("--json", action="store_true",
                        help="shorthand for --format json")
    parser.add_argument("--out", type=Path, help="write to this file instead of stdout")
    parser.add_argument("--full", action="store_true",
                        help="keep nav/boilerplate (disables --only-main-content)")
    parser.add_argument("--self-test", action="store_true",
                        help="check the install against this repo's own sources")
    args = parser.parse_args()

    # Before any output: the default Windows console encoding cannot represent
    # the rupee sign, an em-dash, or any Indic script, and this tool's whole
    # job is to move exactly those characters around.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.self_test:
        return self_test()
    if not args.url:
        parser.error("a URL is required unless --self-test is given")

    result = fetch(args.url, fmt="json" if args.json else args.format,
                   only_main_content=not args.full)
    body = json.dumps(result["data"], indent=2, ensure_ascii=False) \
        if result["format"] == "json" else result["text"]

    if args.out:
        args.out.write_text(body, encoding="utf-8")
        print(f"{result['chars']:,} chars -> {args.out}", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
