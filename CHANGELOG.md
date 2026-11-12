# Changelog

## 0.6.0 (2026-11-12)

- TUI banner: the reactive scene now renders straight from the frame arrays
  (per-cell truecolor, no ANSI round-trip), so the chat console shows the
  same crisp art as the standalone banner. Height is auto: the art can
  never clip again.
- Version stamp unified across python + npm packaging (was 0.5.0 in
  `__init__` vs 0.5.5 in pyproject/package.json).
- README rebuilt around the animated GIF rendered from the live Scene.
- Repo hygiene: generated reports/logs ignored, session scratch untracked,
  MIT LICENSE added.
- Copy pass: every em dash and en dash in the repo replaced with a plain
  hyphen (ASCII-clean strings, no encoding surprises on Windows consoles).

## 0.5.5

- TUI: `!` shell-out, live model name, timestamps, `/exit` quits, skill
  install/uninstall, README GIF renderer.

## 0.5.0

- TUI chat-first redesign: black + violet theme, intent routing, banner as
  rich text, live pipeline HUD.

## 0.4.0

- HTML dossier, live cockpit, impact ranking tiers, keyless module set.

## 0.3.0

- Wildfire engine: lead-driven BFS, WMN username sweep (692 sites), demo
  offline mode, graph hygiene.
