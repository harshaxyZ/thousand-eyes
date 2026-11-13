# thousand-eyes (v0.7) · wildfire edition

[![tests](https://github.com/harshaxyZ/thousand-eyes/actions/workflows/tests.yml/badge.svg)](https://github.com/harshaxyZ/thousand-eyes/actions/workflows/tests.yml)

**one lead in: an impact-ranked evidence dossier out.**

<p align="center">
  <img src="readme_banner.gif" alt="thousand-eyes reactive banner: the eye tracks the cursor, glare sweeps the letters">
</p>

<p align="center">
  <img src="docs/banner.png" alt="thousand-eyes banner screenshot: pixel eye + faceted title, rendered live from the Scene frame arrays">
</p>

<p align="center"><b>OPEN-SOURCE INTELLIGENCE · SEE EVERYTHING. MISS NOTHING.</b></p>

An OSINT self-audit CLI that thinks like an attacker: you feed it identifiers
you own (email, username, phone, URL, domain), it walks every confirmed pivot
outwards: platform accounts, breach exposure, PGP keys, archives, darkweb
index mentions, mail infrastructure. Then it prints what an attacker could
actually DO with it, in ranked order, every line carrying live proof.

## What you get

```
WHAT IT DID (7 stages - each stage's findings became the next stage's leads):
  1. email jane.doe@example.com → 9 finding(s) from 12 module(s) in 21.4s
  2. username janedoe → 14 finding(s) from 18 module(s) in 96.2s
  3. url https://github.com/janedoe → 3 finding(s) from 3 module(s) in 12.1s
  ...

✜  WEAPONISED: identity keys an attacker starts here
   ✜ name: ✔  ✜ second email: ✔  ✜ stealer-log hits
▦  ACCOUNTS CONFIRMED: pivot doors, with confidence tags
   ▦ github.com/...  [confirmed]  ▦ x.com/...  [handle-reuse - same person unconfirmed]
✔  ambient footprint: archives, infra, index mentions
counts ✔ source coverage ✔ stage table ✔ skipped/inconclusive (never faked silent)
```

- **One lead in, the whole presence out.** Every confirmed finding becomes
  the next lead: a profile URL proves the handle it lives on, so the handle
  is checked on every platform, and its new accounts link to new URLs. The
  cascade runs until the frontier is empty or the budget ends - and the
  report says which stage found what. See `docs/PIPELINE.md`.
- **Evidence only.** Every row carries its source, the request that produced
  it and a quotable snippet. Blocked sources stay *inconclusive*; nothing is
  ever silently reported as "not found".
- **It reasons about identity, not just strings.** The same username on two
  platforms is *handle reuse*, not proof of one person. Every confirmed
  account is tagged `[confirmed]` or `[handle-reuse - same person
  unconfirmed]`, and the report states plainly that corroboration (name,
  bio, avatar, cross-links) is required before attributing accounts to a
  single human. Your `janedoe_` on Instagram and someone else's on X are
  two different people, and the tool says so.
- **Failures are visible.** A source that errors, times out or rate-limits
  shows up in a "checks that failed" section with the real reason - never
  laundered into a clean-looking negative. A "source coverage" line reports
  how many checks actually answered, and flags thin coverage as a finding
  in itself. A sweep where a few sites timed out counts as answered "only
  partly", and each dead site is still listed.
- **`--doctor` checks the machine end to end**: python, install location,
  optional deps, LLM key, IDE skill, and a live reachability probe of five
  keyless endpoints.
- **Fenced by code, not by prompt.** No password-reset flows, no credential
  probing, no broker APIs, no breach-dump pulls, no `.onion` resolution. The
  LLM planner cannot bypass any of it: it only *selects* tools; the
  deterministic SafeClient executes them, and its fence is a hook on the
  HTTP client itself, so every request of every tool (redirect hops
  included) passes through it.
- **Reactive banner.** `thousand-eyes` opens with a high-resolution terminal
  banner (quadrant-block renderer, ~3px edge steps): the eye blinks, tracks
  the glare sweep, letters decode in. Same art in the chat TUI, rendered
  straight from the scene's frame arrays.

## Install (one command, copy-paste ready)

**Windows (PowerShell):**

```powershell
powershell -c "irm https://raw.githubusercontent.com/harshaxyZ/thousand-eyes/main/install.ps1 | iex"
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/harshaxyZ/thousand-eyes/main/install.sh | bash
```

That script finds python 3.10+, installs `pipx` if needed (no admin, no
sudo), installs thousand-eyes into an isolated environment, adds `numpy` for
the animated banner, verifies the binary and prints your next steps. Re-run it
anytime to upgrade.

Prefer to drive it yourself?

```bash
pipx install "git+https://github.com/harshaxyZ/thousand-eyes.git"
pipx inject thousand-eyes numpy          # banner only; the audit does not need it
```

It installs from this git repository. There is no PyPI or npm package.

Dependencies (`httpx`, `pyyaml`, `rich`, `textual`) install automatically and
stay pinned. `numpy` is only used by the optional startup banner. Every
dependency follows the repo's 3-phase verification gate (see
`research/dependency-gate-3phase.md`).

**No Docker, no daemons, no cloud.** It is a plain python CLI: install it,
run it in your terminal, it walks public endpoints and prints the dossier
locally. Nothing is uploaded anywhere; the only traffic is read-only GETs to
the first-party public endpoints it audits.

## 60-second setup

```bash
thousand-eyes --setup             # pick a provider, paste a key (gemini free tier or claude)
thousand-eyes --skill install     # teach your IDE agent (Claude Code, ...) how to drive the tool
thousand-eyes --doctor            # is this machine ready? runtime, deps, key, skill, endpoint reachability
thousand-eyes                     # open the chat console
```

`--setup` stores the key in `~/.thousand-eyes/config.json` (mode 0600) and
the chat picks it up instantly. Without a key the chat still answers - it
runs on the built-in offline engine, so the tool is never dead weight.

**Completely free:** every data source the audit uses by default is a
free, keyless, first-party public API - the tool itself never needs an LLM
to produce findings. An LLM only powers the chat and the written analysis.
Optional keys widen the reach, and a source without its key shows up as
`skipped` or falls back, never as a clean result:

| env var | what it unlocks |
|---------|-----------------|
| `SERPER_API_KEY` | Google dorking through serper.dev (free credits, no card) |
| `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID` | Google dorking through the official Programmable Search API |
| `BRAVE_API_KEY` | dorking through the Brave search API |
| `HIBP_API_KEY` | Have I Been Pwned account-level breach check |
| `GITHUB_TOKEN` | higher GitHub rate limits |

## Use

```
thousand-eyes                               # violet chat console + live HUD (the TUI)
thousand-eyes you@example.com               # polite evidence walk
thousand-eyes you@example.com --full        # WILDFIRE: full 692-site sweep + derived handles
thousand-eyes janedoe_ --full               # usernames work too (no email needed)
thousand-eyes you@example.com --json report.json --html report.html --dot graph.dot
thousand-eyes you@example.com --demo        # offline synthetic replay (venue-safe)
thousand-eyes you@example.com --url-log urls.log   # full outbound-URL transparency
thousand-eyes --agent you@example.com       # agentic mode (LLM-planned, see below)
thousand-eyes --demo --agent you@example.com  # agent mode, offline synthetic replay
thousand-eyes you@example.com --budget-seconds 1800   # deeper cascade (quality first)
thousand-eyes you@example.com --max-leads 300         # hard ceiling on nodes visited
thousand-eyes you@example.com --depth 2               # fewer hops from the seed (default 4)
```

- Default mode never invents usernames. `--full` opts into wildfire: bigger
  reach, punctuation variants of your own local-part, complete WMN stroll
  (derived handles still get the polite sweep, so a full run stays ~3 min).
- The interactive CONFIRM gate is gone since v0.4: scripted runs just work
  while the consent statement stays on screen.
- Live cockpit: while scanning you get a real progress bar + ETA + streaming
  ranked findings (rich). Non-tty runs print clean reports only.

### Agent mode (`--agent`)

```
thousand-eyes --agent you@example.com --json agent.json --html dossier.html
```

An LLM planner fires every registry source that answers each lead, audits
what is still "dark" and picks extra tools (site probes, web search),
pivots on discovered leads, and writes the final markdown dossier.

- Key: run `thousand-eyes --setup` (Gemini free tier or Claude). The key
  is looked up in this order: the `--setup` config file, `ANTHROPIC_API_KEY`,
  `~/.gemini/api-key` (one line), `GEMINI_API_KEY`. Claude runs sonnet by
  default, Gemini runs flash-lite.
- Every lead in a round gets every tool for its type, and the tool list is
  generated from `registry.py`, the same list the default mode uses:
  platform lookups, breach and stealer-log checks, the dark-web index and
  the dork sets. The self-audit can add single-site probes and free-text
  searches (`search.google`, `search.bing`, ...).
- `--agent --full` sweeps all 692 sites for the seed usernames.
- Fence is unchanged: no onion resolution, no auth probing, no breach dumps.
- `--agent-budget N` caps LLM calls (default 120); tool calls have their own
  cap of 400 per run. `--model` overrides the model (`claude-*` uses the
  Anthropic key, anything else the Gemini key).
- `--demo --agent` replays the synthetic fixture for the audit sources; the
  planner and the report still call your LLM.

The default mode stays exactly as it was: no LLM, no key, no network beyond
the keyless public endpoints.

## The wildfire model

**Read `docs/PIPELINE.md` for the full flow** - how one lead becomes a
whole online presence, stage by stage.

| lead type | modules |
|-----------|---------|
| email | gravatar, breach-index (HIBP, keyed), xposedornot breach-existence, LeakCheck breach sources, Hudson Rock stealer-log, github-commit-search, userrealm, PGP (keys.openpgp.org), email checks, registration leaves, darkweb index (ahmia), exact-document search, web dorks, mail posture (DoH/crt.sh/RDAP) |
| username | WhatsMyName sweep (692-site dataset), github-profile, gitlab-profile, keybase-proofs, reddit-user, mastodon-lookup, bluesky-profile, hackernews-user, dockerhub-profile, chess-profile, lichess-profile, codeberg-profile, devto-profile, discogs-profile, wikipedia-user, archive-creator, darkweb index, exact-document, web dorks |
| url | fetch + strict extraction (+github interconnect), wayback CDX, security-header posture, web dorks |
| phone | exact-document (quoted search), web dorks |
| domain | DoH MX records + certificate transparency + RDAP, security-header posture, darkweb index, web dorks |

### Search dorking

Each seed is expanded into a fixed set of exact-phrase queries with search
operators: paste sites (`site:pastebin.com OR site:rentry.co ...`), exposed
documents (`filetype:pdf OR filetype:xls ...`), code hosts, leak chatter,
and for a domain `site:`, exposed config files (`ext:env OR ext:sql ...`)
and addresses on the domain.

- **Google** has no keyless endpoint and blocks scraped result pages, so
  Google dorking runs through an official API: set `SERPER_API_KEY`, or
  `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID`. With a key, every dork goes to
  the Google index.
- **Without a key** the dorks rotate over Bing, DuckDuckGo and Mojeek. They
  accept the same operators but block automated queries quickly; a blocked
  query is listed under "checks that failed", never counted as "no results".
- A search hit is a `web mention` row in the ambient tier. It is never
  turned into a new lead: only a page that still contains the identifier
  (the exact-document module fetches and checks it) becomes a pivot.
- Dorks run for seeds only, so a deep cascade does not hammer the engines.

### Dark web and breach exposure

All of it is read over the clear web, from indexes that publish what they
already hold. Nothing here opens a `.onion` connection or downloads a dump.

| source | what it says | key |
|--------|--------------|-----|
| Ahmia index | which Tor hidden services are indexed next to the identifier | none |
| Hudson Rock | whether an infostealer-infected machine held logins for the email, and when | none |
| LeakCheck public | which breaches list the email and which field types leaked | none |
| XposedOrNot | breach names for the email | none |
| Have I Been Pwned | breach names for the email | `HIBP_API_KEY` |
| paste index (`--agent`) | paste-site pages mentioning the identifier | none |

Every confirmed finding becomes a new typed LEAD; leads spawn leads until
spawn-cap bands close. The graph remains reproducible and quotable:
`--json` has every edge's source/evidence/snippet; `--html` renders the
ranked radial graph as a standalone zero-asset file.

## Impact ranking (the attacker POV)

| glyph | tier | meaning |
|-------|------|---------|
| ✜ | weaponised | real names, second emails, phone numbers, stealer-log hits: the keys that unlock people-search, docs, SIM attacks |
| ▦ | pivot doors | confirmed platform accounts and breach records: profiles an attacker visits to harvest bio/contact/graph |
| · | ambient | archives, mail infra, darknet index mentions: context, not doorways |

## Hard rules (enforced in code)

1. Pivots must be confirmed by the response body itself.
2. No password-reset flows, no credential probing, no login-wall brute.
3. No broker APIs, no breach-dump pulls: existence checks only.
4. No spam engines, no fake-account generation.
5. Politeness caps: per-type spawn caps, sweep concurrency + stagger, one
   request per site per lead, darknet searches capped. The modules of one
   lead run side by side (at most 6 at a time), each against its own host.
6. `--url-log` writes every outbound URL, so any run can be audited.
7. Missing keys never read as a clean bill: they render as `skipped` rows.
8. Anything that could go wrong in a demo is a `--demo` run on synthetic
   fixtures: zero sockets in either direction.

## The banner, as a library

The reactive banner is plain python: render one frame, get typed arrays of
glyphs + colors, or print the whole animation.

```python
from thousand_eyes.banner import Scene, frame_to_text, show_banner

scene = Scene(150)                     # fits 150 columns, shrinks automatically
glyphs, fg, bg, bg_default = scene.final_frame()
print(frame_to_text(scene.final_frame()))   # static frame with truecolor ANSI
show_banner(loops=1)                        # full animation, plays once
```

## FAQ

**Windows console shows mojibake or crashes on the banner.**
The banner needs truecolor; Windows Terminal is fine. Non-UTF8 consoles get
a plain-text fallback automatically, and scans never depend on the banner
(numpy is an optional extra).

**How long is a full run?**
It depends on how far the cascade spreads: about a minute for a seed with
few pivots, several minutes for a wide one or a `--full` sweep (692 sites
per seed username). `--budget-seconds` is the ceiling, and `--url-log`
records every request.

**Does it ever touch data I do not own?**
It only walks pivots that the response body itself confirms, and the demo
mode (`--demo`) is a fully offline synthetic replay. Blocked or ambiguous
sources are rendered as inconclusive, never as "not found".

**Where is the interactive CONFIRM gate?**
Gone since v0.4: scripted runs just work; the consent statement stays on
screen and `--url-log` gives you the full outbound trail.

**Found something that weakens the fence?**
See `SECURITY.md`: private advisories only, public issues are for evidence
models and bugs that do not weaken the fence.

## Tests

```
pip install -e ".[dev,banner]"        # once
py -3 -m pytest -q                    # the whole suite is offline (fixture transport)
```

## Dependency gate (supply-chain defense)

Anything new entering the environment passes 3 phases before use:
provenance (canonical PyPI project + maintainer + license + dependency
review), sandboxed install (venv-scoped, pinned ranges), smoke + full test
suite. Protocol + verdicts: `research/dependency-gate-3phase.md`.

## Attribution

`src/thousand_eyes/data/wmn_data.json` is the WhatsMyName dataset by
WebBreacher and the OSINT community, CC-BY-SA 4.0; snapshot taken
2026-11-03. Our checking engine is independent code; license details in
`src/thousand_eyes/data/ATTRIBUTION-WhatsMyName.md`.

Darknet mentions come from Ahmia's public clearnet index (one search GET
per term plus a cached read of its search form; onions listed there are
cited, never contacted).
