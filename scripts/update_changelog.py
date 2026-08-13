"""Regenerate docs/CHANGELOG.md from git history.

Generated rather than hand-written, deliberately. A changelog maintained by
hand drifts: it gets updated inconsistently, and then it reads as
authoritative while being wrong -- which is worse than not having one. Git
already records every change with a message explaining it; this only
reformats that into something readable without `git log`.

So: never edit docs/CHANGELOG.md. Write a good commit message and run this.

Usage:
    python scripts/update_changelog.py
    python scripts/update_changelog.py --limit 40
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "CHANGELOG.md"

# Commit fields separated by characters that will not occur in a message.
_SEP = "\x1f"
_END = "\x1e"


def commits(limit: int) -> list[dict]:
    result = subprocess.run(
        ["git", "log", f"-{limit}", f"--pretty=format:%h{_SEP}%ad{_SEP}%an{_SEP}%s{_SEP}%b{_END}",
         "--date=short"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"git log failed: {result.stderr.strip()}")

    entries = []
    for block in result.stdout.split(_END):
        if not block.strip():
            continue
        parts = block.strip().split(_SEP)
        if len(parts) < 4:
            continue
        sha, date, author, subject = parts[:4]
        body = parts[4] if len(parts) > 4 else ""
        entries.append({"sha": sha, "date": date, "author": author,
                        "subject": subject, "body": body.strip()})
    return entries


def files_changed(sha: str) -> tuple[int, int, int]:
    result = subprocess.run(
        ["git", "show", "--stat", "--format=", sha],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return 0, 0, 0
    summary = lines[-1]
    def pick(word: str) -> int:
        for part in summary.split(","):
            if word in part:
                return int(part.strip().split()[0])
        return 0
    return pick("file"), pick("insertion"), pick("deletion")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30,
                        help="how many commits to include (default 30)")
    args = parser.parse_args()

    entries = commits(args.limit)
    out = [
        "# CHANGELOG",
        "",
        "**Generated from git history — do not edit by hand.**",
        "Run `python scripts/update_changelog.py` after committing.",
        "",
        "A hand-maintained changelog drifts out of sync and then misleads. The",
        "commit message is the source of truth; this is a readable view of it.",
        "",
        "---",
        "",
    ]

    for entry in entries:
        files, added, removed = files_changed(entry["sha"])
        scale = ""
        if files:
            scale = f" — {files} file{'s' if files != 1 else ''}"
            if added or removed:
                scale += f", +{added}/−{removed}"
        out.append(f"## {entry['date']} · {entry['subject']}")
        out.append("")
        out.append(f"`{entry['sha']}`{scale}")
        if entry["body"]:
            # Drop trailer lines (Co-Authored-By etc.) from the readable body.
            body = [ln for ln in entry["body"].splitlines()
                    if not ln.strip().startswith(("Co-Authored-By:", "Signed-off-by:"))]
            text = "\n".join(body).strip()
            if text:
                out.append("")
                out.append(text)
        out.append("")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {OUTPUT} — {len(entries)} commits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
