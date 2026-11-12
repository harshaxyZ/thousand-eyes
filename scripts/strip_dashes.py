#!/usr/bin/env python3
"""Repo hygiene: replace every em dash (U+2014) and en dash (U+2013) with a
plain hyphen so the codebase is ASCII-clean. Spaced dashes keep their spacing.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".git", ".egg-info", "node_modules", ".pytest_cache", ".venv"}
SKIP_EXT = {".pyc", ".gif", ".png", ".jpg", ".pyc", ".bundle"}

REPLACEMENTS = (
    (" \u2014 ", " - "),
    ("\u2014 ", "- "),
    (" \u2014", " -"),
    ("\u2014", "-"),
    (" \u2013 ", " - "),
    ("\u2013 ", "- "),
    (" \u2013", " -"),
    ("\u2013", "-"),
)


def sweep(path: pathlib.Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return None
    new = text
    for old, sub in REPLACEMENTS:
        new = new.replace(old, sub)
    if new != text:
        path.write_text(new, encoding="utf-8", newline="")
        return path.relative_to(ROOT).as_posix()
    return None


def main() -> int:
    changed = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_EXT:
            continue
        hit = sweep(path)
        if hit:
            changed.append(hit)
    print("\n".join(changed) if changed else "already clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
