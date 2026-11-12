# Changelog

## 0.6.1 (2026-11-12)

- Pipeline upscale: three new keyless username modules - GitLab (REST users
  API), Mastodon (mastodon.social account lookup) and Bluesky (public
  AppView getProfile) - wired into the username sweep and the agent tool
  registry, with fixture tests for confirm/silence paths.
- TUI chat rebuilt: it is now a general chat on footprint, breaches,
  brokers, phishing and opsec. A real LLM key gets used first when present;
  otherwise a local knowledge engine answers properly (rotating phrasing,
  off-genre asks refused and steered). No more canned one-liner loops.
- TUI formatting: plain `you` / `agent` tags replace the box art, no
  timestamps, both scrollbars removed and long lines wrap in-frame - the
  whole console is one frame, no scrolling.
- The terminal tab is claimed at startup (OSC title): shows
  "thousand-eyes" instead of "Windows PowerShell" while the TUI runs.
- README: static banner screenshot (docs/banner.png, rendered from the
  Scene frame arrays as a truecolor PNG) next to the animated GIF; the GIF
  now opens on the finished art instead of a near-black first frame.
- CI fix: the test workflow no longer installs unpinned deps next to the
  package's own pins (ResolutionImpossible); version bump 0.6.1.

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
