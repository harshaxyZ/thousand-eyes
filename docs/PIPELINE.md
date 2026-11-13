# The cascade: how one thing becomes a whole presence

thousand-eyes is a **node-spreading auditor**. You give it one thing about
a person. It finds what that thing connects to, treats every confirmed
connection as a new thing to check, and keeps going until the frontier is
exhausted or the budget runs out. Then it tells you exactly what it did and
what it found.

```
        seed: jane.doe@example.com
             │
   ┌─────────┴──────────┐
   ▼                    ▼
gravatar → name       github commit search → github.com/janedoe
   │                    │
   ▼                    ▼
twitter.com/…         profile URL proves the handle "janedoe"
   │                    │
   └─────────┬──────────┘
             ▼
     stage 2: username "janedoe"
             │
   ┌─────────┼─────────┬──────────┬─────────┐
   ▼         ▼         ▼          ▼         ▼
 github   gitlab   bluesky    reddit    692-site WMN sweep
   │         │         │          │         │
   └─────────┴─────────┴──────────┴─────────┘
             ▼
     stage 3: every NEW profile URL those accounts linked to
             │
             ▼
     ... until the frontier is empty or the budget is spent
```

## The stages

| stage | what happens | which modules answer |
|-------|--------------|----------------------|
| 0 - SEED | the operator's identifier is checked by every module that speaks its type | email / username / domain / url / phone arms |
| 1 - DIRECT | the seed's confirmed pivots become leads (a name, a profile URL, a domain) | same arms, per lead type |
| 2 - SPREAD | every confirmed **profile URL** proves the **handle** it lives on, so the handle becomes a lead | the full username arm (15 platform lookups, the 692-site sweep, the dark-web index, exact-document search) |
| 3+ - DEEP | the new accounts link to new URLs, the new URLs prove new handles | repeats until depth/budget stops it |

Stage 2 is the hinge of the whole thing: a confirmed profile URL is proof
that a handle exists on that platform, so the handle is checked everywhere.
That is how one email turns into a map of a person's accounts.

## What the rules still forbid

* **Never invent a handle.** Handles only come from response-confirmed
  profile URLs (gravatar, commit search, a WMN hit) or from the operator.
  Never from the email local-part (that is `--full`, and it is opt-in).
* **Never merge identities.** The same handle on two platforms is *handle
  reuse*, not one person. Accounts are tagged `[confirmed]` or
  `[handle-reuse - same person unconfirmed]`.
* **Never fake a negative.** A blocked or timed-out source is INCONCLUSIVE
  and is listed under "checks that failed" with its real reason.

## The API bridge

`src/thousand_eyes/registry.py` is the single list of every source the tool
can call, and which lead types each one answers. The deterministic engine,
the LLM tool layer and the agent's lead routing are all generated from it,
so a new source is added exactly once and works everywhere.

Inside one lead the modules run concurrently (at most 6 at a time, each
against its own first-party host). Their results are folded into the graph
in registry order, so the same inputs always build the same graph.

```python
ModuleSpec("github-profile", ("username",), _simple(github_profile.run),
           "engine.github-profile", "GitHub profile existence + metadata", ("username",))
```

Search dorks (`web-dorks`) are the one arm that runs for seeds only: search
engines rate-limit fast, and their hits are unverified mentions, so they
are listed as ambient rows and never fed back into the cascade.

## The budgets

Quality first: the cascade is bounded by *time* and *total leads*, not by
how fast it feels.

| knob | CLI flag | default | meaning |
|------|----------|---------|---------|
| wall clock | `--budget-seconds` | 900 | when the cascade stops and says so |
| total leads | `--max-leads` | unlimited | hard ceiling on nodes visited |
| depth | `--depth` | 4 | how many hops from the seed |
| politeness caps | (internal) | url 120, username 24, email 12, domain 16 | spawned leads per type |

The politeness caps exist to protect the sites we query, not to make the
tool fast. Raise the time budget for a deeper sweep.

## What you get back

1. **WHAT IT DID** - every stage: the lead, the modules it ran, the
   findings it produced, how long it took.
2. **THE RANKED DOSSIER** - weaponised identity keys first, then confirmed
   accounts with confidence tags, then the ambient footprint.
3. **WHAT IT FOUND** - every confirmed item with its evidence source.
4. **CHECKS THAT FAILED** - every source that errored, with the real
   reason, and a coverage line telling you how much of the sweep actually
   answered. A source that answered but lost some sub-checks (one site of
   the sweep timing out) is counted as answered "only partly".
