# thousand-eyes 🔥 wildfire edition (v0.4)

One lead in → an impact-ranked evidence dossier out. An OSINT self-audit CLI
that thinks like an attacker: you feed it identifiers you own (email,
username, phone, URL), it walks every confirmed pivot outwards — platform
accounts, breach exposure, PGP keys, archives, darkweb index mentions, mail
infrastructure — then prints what an attacker could actually DO with it,
in ranked order, every line carrying live proof.

```
✜  WEAPONISED — identity keys an attacker starts here
   ✜ name: …  ·  ✜ second email: …  ·  ✖ stealer-log hits
▦  ACCOUNTS CONFIRMED — pivot doors into this identity
   ▦ github.com/...  ·  ▦ twitter.com/...  ·  ✖ Collection1 (2019-01-07)
·  ambient footprint — archives, infra, index mentions
counts · manual next-steps · skipped/inconclusive (never faked silent)
```

## Install (one command)

```
pipx install thousand-eyes       # or: pip install thousand-eyes
```

Dependencies (`httpx`, `pyyaml`, `rich`) install automatically. Nothing
beyond those three reaches your machine; `numpy` is only used by the optional
startup banner. Every dependency follows the repo's 3-phase verification gate
(see `research/dependency-gate-3phase.md`).

## Use

```
thousand-eyes                               # violet chat console + live HUD
thousand-eyes you@example.com                 # polite evidence walk
thousand-eyes you@example.com --full          # WILDFIRE: full 692-site sweep + derived handles + wider caps
thousand-eyes harsha1218_ --full              # usernames work too (no email needed)
thousand-eyes you@example.com --json report.json --html report.html --dot graph.dot
thousand-eyes you@example.com --demo          # offline synthetic replay (venue-safe)
thousand-eyes you@example.com --url-log urls.log    # full outbound-URL transparency
thousand-eyes --agent you@example.com         # agentic mode (LLM-planned, see below)
thousand-eyes --demo --agent you@example.com  # agent mode, offline synthetic replay
```

- Default mode never invents usernames. `--full` opts into wildfire: bigger
  reach, punctuation variants of your own local-part, complete WMN stroll
  (derived handles still get the polite sweep so a full run stays ~3 min).
- The interactive CONFIRM gate is GONE in v0.4 — scripted runs just work
  while the consent statement banner stays.
- Live cockpit: while scanning you get a real progress bar + ETA + streaming
  ranked findings (rich). Non-tty runs print clean reports only.

### Agent mode (`--agent`)

```
thousand-eyes --agent you@example.com --json agent.json --html dossier.html
```

An LLM planner (Google Gemini **flash-lite** — free tier, ~no API key cost)
picks which fenced tool families to fire each round, audits what's still
"dark", pivots on discovered leads, and writes the final markdown dossier.

- Key: set `GEMINI_API_KEY`, or save it to `~/.gemini/api-key` (one line).
  The planner falls back to OpenRouter's free flash-lite tier if
  `OPENROUTER_API_KEY` is set instead.
- Fence is unchanged: the LLM *selects* tools; the deterministic SafeClient
  still executes them. No onion resolution, no auth probing, no breach dumps
  — the LLM can't bypass any of it.
- `--agent-budget N` caps LLM calls (default 120); `--model` overrides the
  model (defaults to `gemini-3.5-flash-lite`).
- `--demo --agent` replays the synthetic fixture with zero network calls.

The default mode stays exactly as it was: no LLM, no key, no network beyond
the keyless public endpoints.

## The wildfire model

| lead type | modules |
|-----------|---------|
| email | gravatar, breach-index (HIBP, keyed), github-commit-search, email-registration sweep, Microsoft userrealm, xposedornot breach-existence, Hudson Rock stealer-log (keyed), PGP (keys.openpgp.org), registration leaves, darkweb index (ahmia), exact-document search, domain posture (DoH/crt.sh/RDAP) |
| username | WhatsMyName sweep (692-site dataset), github-profile, keybase-proofs, reddit-user, darkweb index |
| url | fetch + strict extraction (+github interconnect), wayback CDX |
| phone | exact-document (quoted search) |
| domain | DoH MX records + certificate transparency + RDAP |

Every confirmed finding becomes a new typed LEAD; leads spawn leads until
spawn-cap bands close. The graph remains reproducible and quotable:
`--json` has every edge's source/evidence/snippet; `--html` renders the
ranked radial graph as a standalone zero-asset file.

## Impact ranking (the hacker POV)

| glyph | tier | meaning |
|-------|------|---------|
| ✜ | weaponised | real names, second emails, phone numbers, stealer-log hits — the keys that unlock people-search, docs, SIM attacks |
| ▦ | pivot doors | confirmed platform accounts and breach records — profiles an attacker visits to harvest bio/contact/graph |
| · | ambient | archives, mail infra, darknet index mentions — context, not doorways |

Blocked and ambiguous responses stay **inconclusive** — the report shows how
many sources responded badly and why, never pretending "not found".

## Hard rules (enforced in code)

1. Pivots must be confirmed by the response body itself.
2. No password-reset flows, no credential probing, no login-wall brute.
3. No broker APIs, no breach-dump pulls — existence checks only.
4. No spam engines / no fake-account generation.
5. Politeness caps: per-type spawn caps, sweep concurrency + stagger,
   one request per site per lead, darknet searches capped.
6. `--url-log` writes every outbound URL (audit any run: a --full sweep ≈ 735
   requests in ~3.5 min — measured live 2026-11-04).
7. Missing keys never read as a clean bill — they render as `skipped` rows.
8. Everything that could go wrong in a demo is a `--demo` run on synthetic
   fixtures — zero sockets in either direction.

## Dependency gate (supply-chain defense)

Anything new entering the environment passes 3 phases before use —
provenance (canonical PyPI project + maintainer + license + dependency
review), sandboxed install (venv-scoped, pinned ranges), smoke + full test
suite. Protocol + verdicts: `research/dependency-gate-3phase.md`.

## Tests

```
.venv/Scripts/python -m pytest -q     # 43 passed, 1 skipped (numpy-optional banner)
```

## Attribution

`src/thousand_eyes/data/wmn_data.json` — WhatsMyName dataset by WebBreacher
& the OSINT community, CC-BY-SA 4.0; snapshot taken 2026-11-03. Our checking
engine is independent code; dataset license details in
`src/thousand_eyes/data/ATTRIBUTION-WhatsMyName.md`.

Darknet mentions come from Ahmia's public clearnet index (one GET per term;
onions listed there are cited, never contacted).
