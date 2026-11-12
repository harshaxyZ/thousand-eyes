# thousand-eyes (v0.6) · wildfire edition

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
  in itself.
- **`--doctor` checks the machine end to end**: python, install location,
  optional deps, LLM key, IDE skill, and a live reachability probe of five
  keyless endpoints.
- **Fenced by code, not by prompt.** No password-reset flows, no credential
  probing, no broker APIs, no breach-dump pulls, no `.onion` resolution. The
  LLM planner cannot bypass any of it: it only *selects* tools; the
  deterministic SafeClient executes them.
- **Reactive banner.** `thousand-eyes` opens with a high-resolution terminal
  banner (quadrant-block renderer, ~3px edge steps): the eye blinks, tracks
  the glare sweep, letters decode in. Same art in the chat TUI, rendered
  straight from the scene's frame arrays.

## Install (one command, copy-paste ready)

**Windows (PowerShell):**

```powershell
powershell -c "irm https://raw.githubusercontent.com/janedoe/thousand-eyes/main/install.ps1 | iex"
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/janedoe/thousand-eyes/main/install.sh | bash
```

That script finds python 3.10+, installs `pipx` if needed (no admin, no
sudo), installs thousand-eyes into an isolated environment, adds `numpy` for
the animated banner, verifies the binary and prints your next steps. Re-run it
anytime to upgrade.

Prefer to drive it yourself?

```bash
pipx install "git+https://github.com/harshaxyZ/thousand-eyes.git"   # or: pipx install thousand-eyes
pipx inject thousand-eyes numpy                                     # banner only; the audit does not need it
```

```bash
npm i -g thousand-eyes            # node wrapper around the same python CLI
thousand-eyes you@example.com     # identical output to the pip install
```

The npm package is a thin wrapper: it locates your installed `thousand-eyes`
python package and runs its console script. Publishing the npm package is a
manual step (`npm publish`); the files are in `package.json` + `bin/`.

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

## Use

```
thousand-eyes                               # violet chat console + live HUD (the TUI)
thousand-eyes you@example.com               # polite evidence walk
thousand-eyes you@example.com --full        # WILDFIRE: full 692-site sweep + derived handles
thousand-eyes janedoe_ --full            # usernames work too (no email needed)
thousand-eyes you@example.com --json report.json --html report.html --dot graph.dot
thousand-eyes you@example.com --demo        # offline synthetic replay (venue-safe)
thousand-eyes you@example.com --url-log urls.log   # full outbound-URL transparency
thousand-eyes --agent you@example.com       # agentic mode (LLM-planned, see below)
thousand-eyes --demo --agent you@example.com  # agent mode, offline synthetic replay
thousand-eyes you@example.com --budget-seconds 1800   # deeper cascade (quality first)
thousand-eyes you@example.com --max-leads 300         # hard ceiling on nodes visited
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

An LLM planner (Google Gemini flash-lite: free tier, ~no API key cost) picks
which fenced tool families to fire each round, audits what's still "dark",
pivots on discovered leads, and writes the final markdown dossier.

- Key: set `GEMINI_API_KEY`, or save it to `~/.gemini/api-key` (one line).
  Falls back to OpenRouter's free flash-lite tier if `OPENROUTER_API_KEY` is
  set instead.
- Fence is unchanged: no onion resolution, no auth probing, no breach dumps.
- `--agent-budget N` caps LLM calls (default 120); `--model` overrides the
  model.
- `--demo --agent` replays the synthetic fixture with zero network calls.

The default mode stays exactly as it was: no LLM, no key, no network beyond
the keyless public endpoints.

## The wildfire model

| lead type | modules |
|-----------|---------|
| email | gravatar, breach-index (HIBP, keyed), github-commit-search, userrealm, xposedornot breach-existence, Hudson Rock stealer-log (keyed), PGP (keys.openpgp.org), registration leaves, darkweb index (ahmia), exact-document search, mail posture (DoH/crt.sh/RDAP) |
| username | WhatsMyName sweep (692-site dataset), github-profile, gitlab-profile, keybase-proofs, reddit-user, mastodon-lookup, bluesky-profile, hackernews-user, dockerhub-profile, chess-profile, lichess-profile, codeberg-profile, devto-profile, discogs-profile, wikipedia-user, archive-creator, darkweb index, exact-document |
| url | fetch + strict extraction (+github interconnect), wayback CDX, security-header posture |
| phone | exact-document (quoted search) |
| domain | DoH MX records + certificate transparency + RDAP, security-header posture |

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
   request per site per lead, darknet searches capped.
6. `--url-log` writes every outbound URL (audit any run: a `--full` sweep is
   ~735 requests in ~3.5 min, measured live 2026-11-04).
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
Polite mode: ~1 min. `--full` wildfire: ~3.5 min, ~735 requests, all
recorded in `--url-log`.

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
py -3 -m pytest -q                    # 125 passed
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

Darknet mentions come from Ahmia's public clearnet index (one GET per term;
onions listed there are cited, never contacted).
