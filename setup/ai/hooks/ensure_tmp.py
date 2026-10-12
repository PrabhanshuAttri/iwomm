#!/usr/bin/env python3
"""SessionStart hook for Claude Code.

Ensures <project root>/tmp exists and is gitignored, so downloads have a
scratch area that never gets committed. Prints one line of context for Claude.
Never fails the session: errors go to stderr and exit 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRATCH_DIR = "tmp"
IGNORE_LINE = "/tmp/"
IGNORE_EQUIVALENTS = {"/tmp/", "tmp/", "/tmp", "tmp"}


def project_root(cwd: str) -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(os.path.realpath(env_root))
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return Path(os.path.realpath(out.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(os.path.realpath(cwd))


def ensure_gitignored(root: Path) -> bool:
    gi = root / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    if any(line.strip() in IGNORE_EQUIVALENTS for line in existing.splitlines()):
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with gi.open("a") as f:
        f.write(f"{prefix}\n# Scratch area for downloads (Claude Code)\n{IGNORE_LINE}\n")
    return True


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    root = project_root(payload.get("cwd") or os.getcwd())
    home = Path(os.path.realpath(Path.home()))

    if root == Path("/") or root == home or root in home.parents:
        print(f"Project root is {root}; not creating a scratch dir there.", file=sys.stderr)
        return

    try:
        scratch = root / SCRATCH_DIR
        created = not scratch.exists()
        scratch.mkdir(exist_ok=True)
        ignored = ensure_gitignored(root) if (root / ".git").exists() else False
    except OSError as e:
        print(f"ensure_tmp: {e}", file=sys.stderr)
        return

    notes = []
    if created:
        notes.append("created")
    if ignored:
        notes.append("added /tmp/ to .gitignore")
    suffix = f" ({', '.join(notes)})" if notes else ""
    print(f"Download scratch dir: {scratch}{suffix}. All downloads go here before entering the project.")


if __name__ == "__main__":
    main()
