#!/usr/bin/env python3
"""SessionStart hook for Claude Code.

Ensures <project root>/tmp exists and holds a `.gitignore` of `*`, so downloads
have a scratch area that never gets committed. The ignore rule lives inside
tmp/ so the root .gitignore is never edited and no path pattern can be misread
as the system /tmp. Prints one line of context for Claude.
Never fails the session: errors go to stderr and exit 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRATCH_DIR = "tmp"
# `*` also matches this .gitignore itself, so nothing under tmp/ is ever tracked.
IGNORE_ALL = "*\n"


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


def ensure_gitignored(scratch: Path) -> bool:
    # Leave an existing tmp/.gitignore alone: the user may have customized it.
    try:
        with (scratch / ".gitignore").open("x") as f:
            f.write(IGNORE_ALL)
    except FileExistsError:
        return False
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
        # Written even outside a git repo, so a later `git init` already ignores tmp/.
        ignored = ensure_gitignored(scratch)
    except OSError as e:
        print(f"ensure_tmp: {e}", file=sys.stderr)
        return

    notes = []
    if created:
        notes.append("created")
    if ignored:
        notes.append("wrote tmp/.gitignore")
    suffix = f" ({', '.join(notes)})" if notes else ""
    print(f"Download scratch dir: {scratch}{suffix}. All downloads go here before entering the project.")


if __name__ == "__main__":
    main()
