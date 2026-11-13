# Changelog

## 0.7.0 (2026-11-13)

- **The cascade is the product now.** One lead in, and every confirmed
  finding becomes the next lead: the run keeps walking the person's online
  presence node by node until the frontier is exhausted or the budget ends.
  The engine - not an LLM - decides what a finding is; the LLM only writes
  the analysis over the real evidence afterwards (and is optional).
- **The hinge that makes it spread:** a response-confirmed profile URL
  proves the handle it lives on, so the handle becomes the next lead and
  the sweep continues from there. 692 WhatsMyName templates plus a table
  of platforms the dataset does not cover (bluesky, x, chess, lichess,
  codeberg, dev.to, docker hub, keybase, hacker news) drive the extraction.
  Handles are never invented - only taken from confirmed URLs.
- **The API bridge** (`src/thousand_eyes/registry.py`): one list of every
  source the tool can call and which lead types each one answers. The
  deterministic engine and the LLM tool layer are both generated from it,
  so a new source is added once and works everywhere.
- **Six more keyless platforms** (live-verified before shipping): Lichess,
  Codeberg, DEV Community, Discogs, Wikipedia accounts and Internet
  Archive creator credits - 32 modules in total, 18 of them for usernames.
- **Live cascade feed in the TUI:** every stage, every module call and
  every finding as it happens ("stage 2 · username -> janedoe",
  "found name: Jane Doe"), in a scrolling panel instead of a blank box.
- **The report says what it did:** a stage table (lead, modules, findings,
  seconds per stage) next to the ranked dossier, the coverage line and the
  failed-check list.
- **Honest confidence tags by provenance:** an account reached only through
  a handle match is `[handle-reuse - same person unconfirmed]`; one
  reached through the seed's own evidence (a commit, a gravatar entry) is
  `[confirmed]`.
- **Budgets instead of speed knobs:** `--budget-seconds` (default 900) and
  `--max-leads` bound the cascade, and the report says exactly where it
  stopped. Quality first - a deep sweep takes minutes on purpose.
- Bug fixes found by live runs: chess.com returns `joined` as a unix
  timestamp (not a string); the model shown in the status line is now the
  model that will actually answer (never a hardcoded default).
- **Free by design:** every data source is a free, keyless, first-party
  public API, so the audit never needs an LLM - only the chat and the
  written narrative do. Google's "free tier not available for this
  project" 403 now explains the real fixes instead of printing a code.

## 0.6.2 (2026-11-12)

- One-line install that actually works for a newcomer: `install.ps1`
  (Windows) and `install.sh` (macOS/Linux) find python 3.10+, install pipx
  if missing, install the CLI from the repo, inject numpy for the banner,
  verify the binary and print the next steps. No admin, no sudo, no
  Docker, no daemons - a plain terminal CLI.
- Guided key onboarding: `thousand-eyes --setup` (or `/setup` in the TUI)
  stores a provider + key in `~/.thousand-eyes/config.json` (mode 0600);
  the provider layer reads it first, so chat and `--agent` come alive
  immediately. Without a key the chat still answers on the offline engine.
- IDE-agent skill: `thousand-eyes --skill install` writes a SKILL.md
  (with the evidence rules and the fence) into
  `~/.claude/skills/thousand-eyes/`, so an IDE agent knows when and how
  to drive the tool.
- Identity reasoning: a matching handle on several platforms is handle
  reuse, not proof of one person. Confirmed accounts are tagged
  `[confirmed]` or `[handle-reuse - same person unconfirmed]`, and the
  report states that corroboration (name, bio, avatar, cross-links) is
  required before attributing accounts to one identity. The agent report
  prompt and the chat brain follow the same rule.
- Failures are first-class: a "checks that failed" section lists every
  errored source with its real reason, instead of a single inconclusive
  footnote. A "source coverage" line reports how many checks actually
  answered (and flags thin coverage as a finding in itself). The HTTP layer
  takes one conservative connect-level retry (never on 429, never
  re-issuing a request that reached a server).
- `thousand-eyes --doctor`: machine readiness in one command - runtime,
  install location, optional deps, LLM key, IDE skill and a live
  reachability probe of five keyless endpoints.
- Bug fix found by a live run: chess.com returns `joined` as a unix
  timestamp int (not a string) - the module now normalises both forms.
- Four more keyless sources: Hacker News (Algolia), Docker Hub,
  Chess.com, and a first-party HTTP security-header posture probe for the
  operator's own url/domain lead.
- Boot: the CLI no longer imports the scan stack (httpx) or the banner
  until it is needed - `--version`, `--help`, `--setup` and `--skill` now
  start in ~0.6s instead of ~1.7s.

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
