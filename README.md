```
            __          __
            \ \        / /
             \ \  /\  / /
              \ \/  \/ /
               \  /\  /
                \/  \/
```
<sub>(stylized "W" mark, in the spirit of WordPress's purple branding — hand-drawn ASCII, not the trademarked logo)</sub>

# thousand-eyes

Deterministic public-surface self-audit CLI. Point it at an identifier you
own or are authorized to review — an email address, an E.164 phone number,
or an `https://` URL — and it reports what's publicly discoverable about it:
Gravatar profile data, breach exposure (HIBP), real commit-authorship
accounts on GitHub, confirmed account registrations on a short list of
sites, DNS/certificate-transparency posture for the email's domain, and
exact-match web pages that pivot to further evidence — as a depth-2
pivot/leaf graph.

No LLMs or AI models anywhere in the pipeline. Every finding is source-linked
to a fetched artifact (URL + exact snippet). A blocked, timed-out, or
ambiguous check is reported as inconclusive — never collapsed into a false
"not found." Nothing is derived from string similarity or guessed from an
email's local-part; an edge exists only because a specific request returned
that specific identifier.

## How it works

Eight fixed modules run in order, no dynamic registry:

1. **normalise** — classifies the input as email / E.164 phone / https URL, or rejects it.
2. **gravatar** — MD5-hash lookup against Gravatar's public profile JSON.
3. **exact-document** — searches (Brave or Google CSE, if a key is configured) for the exact quoted seed, fetches each result, and only treats a page as a pivot if the live page body still contains the exact seed.
4. **hibp** — Have I Been Pwned breach check (requires `HIBP_API_KEY`), reported as pivots/leaves depending on whether the breach payload includes a username.
5. **github-commit-search** — queries GitHub's public commit-search API for `author-email:<seed email>` (an exact field match against GitHub's real commit history, not a local-part guess) and reports the real GitHub account login of any commit author/committer GitHub itself attributes to that exact email. No key required; works for any email seed, not just ones GitHub already surfaced via gravatar/HIBP.
6. **registration-leaves** — a short, hand-verified YAML list (`src/thousand_eyes/data/registration_sites.yaml`) of sites with a known registered/absent response marker. A confirmed registration is a leaf, never a pivot, and is never re-expanded.
7. **dns** — for non-free-mailbox domains, MX records (via DNS-over-HTTPS) and certificate-transparency hostnames (via crt.sh), reported as a "mail infrastructure" leaf list, never as identity pivots.
8. **fetch** — one GET + one extraction pass (https URLs, emails, E.164 phones) per discovered URL, used for both depth-1 and depth-2 expansion.

Plus one conditional interconnect step that only fires when an earlier
module already returned a URL that is itself exactly a GitHub profile root
(`https://github.com/<user>`) — never a username guessed from the email
local-part:

- **github-profile** — calls GitHub's public user API (optionally with a `GITHUB_TOKEN` for a higher rate limit) and reports only fields the account actually has set: real name, personal site, Twitter handle.
- **keybase-proofs** — calls Keybase's public, keyless proof-lookup API for that same confirmed GitHub username and reports each cryptographically-signed, currently-live proof (Twitter, GitHub, Reddit, Hacker News, personal website) as a new pivot. Each pivot's evidence URL is the proof's own independently-checkable URL, not Keybase's lookup URL — `dns`-type proofs are skipped because Keybase's own API only gives back a URL into its own sigchain viewer for those, not a third-party-checkable one.

The graph has exactly two node classes: **pivot** (a response contained a new
identifier) and **leaf** (a response only confirmed registered/not
registered). Depth is capped at 2 for URL-fetch expansion — a depth-2 URL is
recorded as a node but is never itself fetched. The github-profile /
keybase-proofs step is account-interconnection via a real lookup API, not
URL-fetch recursion, so it runs once per confirmed GitHub username
regardless of that username's URL's fetch depth.

## Install

```bash
pip install -e .
```

Requires Python >=3.10. Only dependencies: `httpx`, `pyyaml`.

## Usage

```bash
thousand-eyes you@example.com
thousand-eyes +15551234567
thousand-eyes https://example.com/you

thousand-eyes you@example.com --json report.json   # write {nodes, edges, leaves, errors}
thousand-eyes you@example.com --dot graph.dot       # write a Graphviz .dot file
thousand-eyes you@example.com --fold-plus           # strip a +tag from the email local-part first
```

Default output is plain text: the pivot path (with evidence URL + matched
snippet per edge), an "also registered on" leaf list, a "skipped" list
(modules with no configured key), and an "errors" list (module name + exact
error, never a guess). If there is no pivot beyond the seed, it prints
`<seed>: no public pivot` and exits 0.

## Optional API keys

All of these are optional. Without them the relevant module prints
`skipped`, not an error.

| Variable | Used by |
|---|---|
| `HIBP_API_KEY` | hibp |
| `BRAVE_API_KEY` | exact-document (preferred if both are set) |
| `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID` | exact-document (fallback) |
| `GITHUB_TOKEN` | github-profile (optional — raises GitHub's 60 req/hr unauthenticated rate limit; the module runs fine without it) |

`github-profile` and `keybase-proofs` need no key at all — both call
keyless public APIs, so the interconnect step works out of the box.

## Self-audit boundary

Only scan identifiers you own or are explicitly authorized to review. This
tool makes real outbound requests to public sources — it does not simulate
anything, does not scrape data brokers, and never attempts logins, password
resets, or breach-dump retrieval.

## Testing

```bash
pytest
```

All tests run against an in-process `httpx.MockTransport` fixture server —
no live network calls happen during the test suite, including the mandated
acceptance tests in `tests/test_acceptance.py`.
