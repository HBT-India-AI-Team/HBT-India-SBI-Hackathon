#!/usr/bin/env python3
"""Zip up the YONO 3.0 project for sharing with a teammate.

Includes source/config/docs; excludes build artifacts, caches, and runtime
data. .env files ARE included by default (they hold live API keys) -- this
zip is only as safe as wherever you send it, so use a channel you trust your
teammate to keep private. Run from anywhere; it locates the project root
from this script's own location.

Usage:
    python zip_project.py                    # writes <project>_<timestamp>.zip next to the project folder
    python zip_project.py --output out.zip    # custom output path
    python zip_project.py --exclude-env       # leave .env files out instead (old safer default)
    python zip_project.py --include-uploads   # also include backend/data/uploads (excluded by default: may hold test PII)
"""

import argparse
import fnmatch
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Directory basenames pruned everywhere they occur (never descended into).
EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "dist-ssr",
    ".pytest_cache",
    ".idea",
    "logs",
    ".vite",
}

# Relative paths (POSIX-style, project-root-relative) of specific
# directories to prune -- narrower than EXCLUDE_DIR_NAMES because the bare
# name ("uploads") is too generic to blanket-exclude everywhere.
EXCLUDE_DIR_PATHS = {
    "backend/data/uploads",
}

# Filename glob patterns excluded wherever they occur.
EXCLUDE_FILE_GLOBS = [
    "*.pyc",
    "*.pyo",
    "*$py.class",
    "*.egg-info",
    ".DS_Store",
    "Thumbs.db",
    "Desktop.ini",
    "*.swp",
    "*.swo",
    "*.local",
    "*.log",
    "npm-debug.log*",
    "yarn-debug.log*",
    "yarn-error.log*",
    "pnpm-debug.log*",
    "lerna-debug.log*",
    "*.suo",
    "*.ntvs*",
    "*.njsproj",
    "*.sln",
]

# Specific runtime-data files excluded by relative path (not just basename,
# since e.g. "synthesized.wav" is only ever noise under this one folder).
EXCLUDE_FILE_PATHS = {
    "backend/data/yono.db",
    "backend/data/yono.db-journal",
    "backend/data/yono.db-wal",
    "backend/data/yono.db-shm",
    "reference/voice_ai_server_client/synthesized.wav",
}

# .vscode is pruned like any other excluded dir EXCEPT this one file.
VSCODE_KEEP_FILE = "extensions.json"


def is_env_file(name: str) -> bool:
    """.env / .env.* (.env.example is always just a placeholder template,
    never treated as a secret regardless of the --exclude-env flag)."""
    if name == ".env.example":
        return False
    return name == ".env" or name.startswith(".env.")


def should_skip_dir(rel_path: str, name: str) -> bool:
    if name in EXCLUDE_DIR_NAMES:
        return True
    if rel_path in EXCLUDE_DIR_PATHS:
        return True
    return False


def should_skip_file(rel_path: str, name: str) -> bool:
    if rel_path in EXCLUDE_FILE_PATHS:
        return True
    if any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILE_GLOBS):
        return True
    return False


def collect_files(root: Path, include_uploads: bool, exclude_env: bool):
    env_files = []
    included = []

    exclude_dir_paths = set(EXCLUDE_DIR_PATHS)
    if include_uploads:
        exclude_dir_paths.discard("backend/data/uploads")

    for dirpath, dirnames, filenames in root.walk() if hasattr(root, "walk") else _walk_compat(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        # Prune excluded subdirectories in place so os.walk never descends
        # into them (keeps this fast even with a 90MB+ node_modules around).
        keep = []
        for d in dirnames:
            d_rel = f"{rel_dir}/{d}" if rel_dir else d
            if d == ".vscode":
                keep.append(d)  # descend, but filter its contents below
                continue
            if d in EXCLUDE_DIR_NAMES or d_rel in exclude_dir_paths:
                continue
            keep.append(d)
        dirnames[:] = keep

        in_vscode = rel_dir == ".vscode" or rel_dir.endswith("/.vscode")

        for f in filenames:
            f_rel = f"{rel_dir}/{f}" if rel_dir else f
            if in_vscode and f != VSCODE_KEEP_FILE:
                continue
            if is_env_file(f):
                env_files.append(f_rel)
                if exclude_env:
                    continue
                included.append(f_rel)
                continue
            if should_skip_file(f_rel, f):
                continue
            included.append(f_rel)

    return included, env_files


def _walk_compat(root: Path):
    """Path.walk() is 3.12+; fall back to os.walk for older Pythons."""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        yield dirpath, dirnames, filenames


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", "-o", help="Output .zip path (default: alongside the project, timestamped)")
    parser.add_argument(
        "--exclude-env",
        action="store_true",
        help="Leave .env files out of the zip instead of including them (they hold live API keys)",
    )
    parser.add_argument(
        "--include-uploads",
        action="store_true",
        help="Also include backend/data/uploads (excluded by default -- may hold test-uploaded documents)",
    )
    args = parser.parse_args()

    project_name = PROJECT_ROOT.name
    if args.output:
        out_path = Path(args.output).resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = PROJECT_ROOT.parent / f"{project_name}_{timestamp}.zip"

    if out_path.resolve().is_relative_to(PROJECT_ROOT):
        print(f"Refusing to write the zip inside the project tree ({out_path}) -- pick an --output outside it.")
        sys.exit(1)

    included, env_files = collect_files(PROJECT_ROOT, args.include_uploads, args.exclude_env)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in included:
            zf.write(PROJECT_ROOT / rel, arcname=f"{project_name}/{rel}")

    total_bytes = sum((PROJECT_ROOT / rel).stat().st_size for rel in included)
    zip_bytes = out_path.stat().st_size

    print(f"Wrote {out_path}")
    print(f"  {len(included)} files, {total_bytes / 1_048_576:.1f} MB uncompressed -> {zip_bytes / 1_048_576:.1f} MB zipped")
    print()
    if args.exclude_env:
        print(f"Excluded {len(env_files)} .env file(s) (not zipped -- they hold live API keys):")
        for rel in env_files:
            print(f"  - {rel}")
        print()
        print("Send those keys to your teammate through a separate channel, or drop --exclude-env")
        print("to bundle them in next time.")
    else:
        print(f"Included {len(env_files)} .env file(s) WITH their live API keys:")
        for rel in env_files:
            print(f"  - {rel}")
        print()
        print("This zip now contains real credentials (FinGuru/voice-server/Sarvam keys) in plain")
        print("text -- only send it somewhere you trust to stay private (not a public channel/repo).")
        print("Re-run with --exclude-env if you'd rather share keys separately instead.")
    if not args.include_uploads:
        print()
        print("backend/data/uploads was skipped by default (may hold test-uploaded documents).")
        print("Re-run with --include-uploads if your teammate needs those specific files.")


if __name__ == "__main__":
    main()
