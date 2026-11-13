# Changelog

## Unreleased

- **Optional Tor pass for the dark-web index** (`--tor`, `--tor-proxy`):
  the same Ahmia search also runs over Tor against Ahmia's onion mirror, so
  the lookup survives the clearnet host being blocked. It stays a mention
  check (an indexed onion is cited, never contacted; no dump is fetched).
  The clearnet pass always runs first, so a missing or offline Tor only
  shows up as a failed check, never hiding the clearnet result. SOCKS is an
  optional extra (`pipx inject thousand-eyes socksio`). Domains are searched
  on the index too, not only emails and usernames.
- **Search dorking as a registry source** (`modules/dorks.py`): every seed
  is expanded into exact-phrase dorks (paste sites, exposed documents,
  code hosts, leak chatter, `site:` and exposed-file sets for a domain).
  Google through `SERPER_API_KEY` or `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID`;
  otherwise Bing, DuckDuckGo and Mojeek in rotation. Hits are `web mention`
  rows, never pivots. Because it is a registry source it runs in the
  default cascade, the TUI and the agent (`dork.email`, `dork.username`,
  `dork.domain`, `dork.phone`, `dork.url`).
- **LeakCheck public index** (keyless): which breaches list an email and
  which field types leaked. Names only, never records.
- **Hudson Rock is keyless now.** The module needed a key and read a field
  the live API no longer returns, so it never reported anything. It uses
  the free osint-tools endpoint and the current response shape.
- **The dark-web index works again.** Ahmia rotates its hidden form token;
  the hardcoded one had expired, so every search came back inconclusive.
  The token is now read from the live search form. Domains are searched
  too, not only emails and usernames.
- Agent: `search.google` and `search.bing` tools; `--agent --full` sweeps
  all 692 sites for seed usernames; breach, darknet and infrastructure rows
  are kept as separate ledger rows (they used to collapse into one); the
  paste-index tool reports a blocked search as inconclusive.
- **Agent mode walks every lead.** A round used to fire each tool once, for
  the first lead of its kind only; every lead in the round now gets every
  tool for its type. Leads are remembered across rounds, so one is never
  walked twice.
- **Agent routing is generated from the registry.** The hand-written tool
  list named three tools that did not exist (`engine.dns`, `engine.domain`,
  `engine.darkweb-index`) and skipped 12 of the 16 username sources. The
  self-audit prompt now lists exact tool names, so the model can pick site
  probes and search tools.
- **The fence covers every request.** It is a request hook on the agent's
  HTTP client: engine, search, dork and dark tools (and redirect hops) pass
  through the onion/auth-path block and the per-domain bucket, not only the
  site probes. A fenced URL is an inconclusive result, never a crash.
- **Modules of one lead run concurrently** (6 at a time) and are folded into
  the graph in registry order, so runs are faster and still reproducible.
- **Partial is not failed.** A sweep with a few dead sites is counted as
  answered "only partly" in the coverage line; the dead sites stay listed
  under "checks that failed".
- `--agent-budget` now caps LLM calls only; tool calls have their own cap
  (400) that is applied before a batch fires.
- `--depth` defaults to 4 in the CLI, same as the TUI and the docs (was 3).
- `--model claude-*` uses the Anthropic key and any other model the Gemini
  key; a key for one provider is never sent to the other.
- Fixed: the dork tools raised on their first hit; the breach counter in
  the agent analytics never moved; the per-domain bucket stalled every host
  while one waited; a crashing tool took the whole batch down.
- The User-Agent carries the real version instead of a hardcoded 0.4.
- Removed the npm wrapper (`package.json`, `bin/`): it was never published.
  Install with pipx from git. The install one-liners point at the real
  repository.
- `pytest` works from a plain checkout (`pythonpath = src`).

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
