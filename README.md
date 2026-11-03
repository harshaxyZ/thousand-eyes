# thousand-eyes 🔥 wildfire edition (v0.3)

One lead in → a spider-web of public evidence out. An evidence-only OSINT
self-audit CLI: you feed it identifiers you own (email, username, phone,
URL), it walks every confirmed pivot outwards — accounts, breach existence,
PGP keys, archives, mail infrastructure — and prints the full graph with
per-edge proof. Zero AI guessing: **every edge carries a source, an evidence
URL and the exact matched snippet.**

```
pip install -e .            # runtime: httpx + pyyaml only (numpy optional, for the banner)
thousand-eyes you@example.com janedoe_            # multiple leads, wildfire engine
thousand-eyes --demo you@example.com                 # offline synthetic replay (venue-safe)
thousand-eyes you@example.com --depth 3 --wild       # uncensored breadth (opt-in)
thousand-eyes you@example.com --json report.json --dot graph.dot
thousand-eyes you@example.com --url-log urls.log     # full outbound-URL transparency
```

## The wildfire model

Old tool: fixed 8-step pipeline, one seed, depth 2. New tool: a lead-driven
breadth-first walk — every confirmed finding becomes a new typed LEAD, leads
keep spawning leads:

| lead type | modules |
|-----------|---------|
| email | gravatar, breach-index (HIBP), github-commit-search, email-registration sweep, microsooft userrealm, xposedornot breach-existence, PGP (keys.openpgp.org), duolingo family (registration leaves), domain posture (MX/crt.sh/RDAP), exact-document search |
| username | WhatsMyName sweep (692-site dataset), github-profile, keybase-proofs, reddit-user |
| url | fetch + extraction (+github interconnect), wayback CDX |
| phone | exact-document (quoted search) |
| domain | DoH MX records + certificate transparency + RDAP |

Every module obeys the same evidence law:

- a node exists only because a specific request returned that identifier
- blocked / ambiguous responses are **inconclusive, never "not found"**
- usernames are never invented from thin air — they come from: leads you
  typed, handles real APIs returned, or (opt-in `--wild`) deterministic
  punctuation variants of your own local-part

## Verified keyless sources (live-probed 2026-11-03)

Gravatar profiles · GitHub commit-search + profile APIs · Keybase lookup ·
Twitter/X email-availability JSON · Microsoft userrealm · XposedOrNot ·
keys.openpgp.org · Reddit .json (may 403 — reported, never hidden) ·
Wayback CDX · Cloudflare DoH · crt.sh (flaky → degrade, not crash) ·
rdap.org · Bing HTML search (browser UA; result-redirect decoding built in) ·
WhatsMyName dataset (CC-BY-SA 4.0, bundled with attribution).

Optional keys: `HIBP_API_KEY`, `SERPER_API_KEY` (2,500 free credits — search
insurance), `BRAVE_API_KEY`, `GITHUB_TOKEN`, `HUDSON_API_KEY`.

## Hard rules (enforced in code, not just docs)

1. Pivots must be confirmed by the response body itself.
2. No password-reset flows, no credential probing, no login-wall brute.
3. No broker APIs, no breach-dump pulls — existence checks only.
4. No spam engines / no fake-account generation.
5. politeness caps: per-type spawn caps, sweep concurrency 6, one request
   per site per lead.
6. `--url-log` writes every outbound URL (audit any run).
7. Missing keys can never read as a clean bill — they're `skipped` / `error`.

## Demo day

`--demo` replays a **synthetic fictional subject** through the whole engine
via an in-process MockTransport — zero network, zero privacy risk, full
graph output. Perfect for projector screenshots. For live self-audit on
your own identifiers, drop `--demo`.

## Tests

```
.venv/Scripts/python -m pytest -q     # 33 passed, 1 skipped (numpy-optional banner)
```

## Attribution

`src/thousand_eyes/data/wmn_data.json` — WhatsMyName dataset by WebBreacher
& the OSINT community, CC-BY-SA 4.0; snapshot taken 2026-11-03. Our checking
engine is independent code; dataset license details in
`src/thousand_eyes/data/ATTRIBUTION-WhatsMyName.md`.
