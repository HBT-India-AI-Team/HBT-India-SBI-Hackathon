"""Regenerate docs/API.md from the running FastAPI app.

Generated, not hand-written, for the same reason the changelog is: a
hand-maintained endpoint list drifts, and then it reads as authoritative
while being wrong — which is worse than not having one. This walks
`backend.main:app` and prints what actually exists.

The reason it exists at all: a client team was calling us with a field name
we did not read and a flag nested a level below where we looked for it. Both
were invisible from our side and from theirs. A definitive list of what we
expose is the thing you diff a client's calls against.

So: never edit docs/API.md. Change the routes and run this.

Usage:
    python scripts/dump_api_surface.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
OUTPUT = REPO_ROOT / "docs" / "API.md"

# Which endpoints matter to whom. Anything not matched here is listed under
# "everything else" rather than being silently dropped.
AUDIENCE = [
    ("/agents/{agent_id}/invoke", "**client integrations** — the voice client uses this one"),
    ("/agents/{agent_id}/chat", "**client integrations** — session-managed alternative"),
    ("/embed/{agent_id}", "client integrations — drop-in chat page"),
    ("/healthz", "anyone — liveness"),
]

SKIP_PREFIXES = ("/openapi", "/docs", "/redoc")


def routes() -> list[dict]:
    """Read the OpenAPI schema, not `app.routes`.

    They disagree. Walking `app.routes` found 11 endpoints and none of the 24
    under /admin, because routes pulled in by include_router do not surface
    the same way. `app.openapi()` is the contract FastAPI actually publishes
    and serves, so it is the one to trust — it reports all 35.
    """
    from backend.main import app

    found = []
    for path, operations in app.openapi().get("paths", {}).items():
        if path.startswith(SKIP_PREFIXES):
            continue
        verbs, summary, keyed = [], "", False
        for verb, op in operations.items():
            if verb.upper() in {"HEAD", "OPTIONS"} or not isinstance(op, dict):
                continue
            verbs.append(verb.upper())
            summary = summary or _first_sentence(op.get("description") or op.get("summary") or "")
            # The whole auth model is one header. Anything declaring it is gated.
            keyed = keyed or any(
                p.get("name") == "x-api-key"
                for p in op.get("parameters", []) if isinstance(p, dict)
            )
        if verbs:
            found.append({"path": path, "verbs": sorted(verbs),
                          "summary": summary, "keyed": keyed})
    return sorted(found, key=lambda r: r["path"])


def _first_sentence(text: str) -> str:
    """One sentence, pipes escaped.

    Docstrings here contain JSON examples with `|` in them, which silently
    breaks a markdown table into gibberish rather than failing loudly.
    """
    flat = " ".join(text.split())
    for stop in (". ", " — ", ": "):
        if stop in flat:
            flat = flat.split(stop)[0]
            break
    return flat[:130].replace("|", "\\|").rstrip()


def main() -> int:
    found = routes()
    public, admin, other = [], [], []
    for route in found:
        if route["path"].startswith("/admin"):
            admin.append(route)
        elif any(route["path"].startswith(p.split("{")[0]) for p, _ in AUDIENCE):
            public.append(route)
        else:
            other.append(route)

    out = [
        "# API — every endpoint this backend exposes",
        "",
        "**Generated from `backend.main:app` — do not edit by hand.**",
        "Run `python scripts/dump_api_surface.py` after changing any route.",
        "",
        "This exists to be diffed against what a client is actually calling. A",
        "client team was hitting us with a field name we did not read and a flag",
        "nested a level below where we looked — both invisible from either side.",
        "",
        "For *who* calls what, and the request shapes they send, see",
        "[INTEGRATIONS.md](INTEGRATIONS.md). This file is only what exists.",
        "",
        "`key` = requires the agent's `X-API-Key` header.",
        "",
        "---",
        "",
        "## For client integrations",
        "",
        "These are the only ones an outside team should be calling.",
        "",
    ]

    def table(rows: list[dict]) -> list[str]:
        lines = ["| method | path | key | what |", "|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| `{'`, `'.join(r['verbs'])}` | `{r['path']}` | "
                f"{'yes' if r['keyed'] else '—'} | {r['summary'] or ''} |"
            )
        return lines

    out += table(public)
    out += [
        "",
        "---",
        "",
        "## Internal — the Playground UI",
        "",
        "No API key. Same trust level as a local dev tool; the frontend is the",
        "only thing that should touch these.",
        "",
    ]
    out += table(admin)
    if other:
        out += ["", "---", "", "## Everything else", ""]
        out += table(other)

    out += [
        "",
        "---",
        "",
        "## Checking what a client is really hitting",
        "",
        "Uvicorn logs every request. A wrong path shows up as a 404 against a",
        "path that is not in the tables above:",
        "",
        "```bash",
        "# what has been called, most-hit first",
        "grep -o '\"[A-Z]* /[^ ]*' uvicorn_out.log | sort | uniq -c | sort -rn",
        "",
        "# only the failures — a 404 here is usually a wrong path or a typo'd agent_id",
        "grep -E '\" (4|5)[0-9][0-9] ' uvicorn_out.log",
        "```",
        "",
        "A request that reaches the right path but carries the wrong *field names*",
        "will show as `200 OK` here and still do nothing useful. That class of",
        "failure is only visible in `logs/ollama_calls.jsonl`, which holds the full",
        "prompt actually sent to the model.",
        "",
    ]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {OUTPUT} — {len(found)} routes "
          f"({len(public)} client, {len(admin)} admin, {len(other)} other)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
