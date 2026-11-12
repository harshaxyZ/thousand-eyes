"""Repo hygiene: the copy rule (no em/en dashes) is enforced, not aspirational."""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

TEXT_EXT = {
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".js", ".txt", ".cfg", ".ini",
}


def tracked_text_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    files = []
    for line in out.stdout.splitlines():
        p = ROOT / line
        if p.suffix.lower() in TEXT_EXT:
            files.append(p)
    return files


def test_no_em_or_en_dashes_in_tracked_text():
    offenders = []
    for path in tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "\u2014" in text or "\u2013" in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, "em/en dashes found (use plain hyphens): " + ", ".join(offenders)
