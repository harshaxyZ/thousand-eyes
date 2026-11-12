"""Install thousand-eyes as a skill for IDE agents (Claude Code et al).

A "skill" is a SKILL.md the agent reads so it knows the tool exists, when
to reach for it, and how to drive it safely. We write one into the user's
skill directories; the IDE picks it up on the next session.

Layout: ~/.claude/skills/thousand-eyes/SKILL.md (Claude Code convention).
--scope all also writes the generic ~/.agents/skills/thousand-eyes/SKILL.md.
"""

from __future__ import annotations

import os

from thousand_eyes import __version__

SKILL_NAME = "thousand-eyes"

SKILL_MD = f"""---
name: thousand-eyes
description: OSINT self-audit CLI - one lead in (email, username, domain,
  url or phone), an impact-ranked evidence dossier out. Use when the user
  asks to audit their own digital footprint, check breach/broker exposure,
  find their public accounts, or investigate a domain or URL's public
  surface. Read-only, fenced, evidence-only.
---

# thousand-eyes

Version {__version__}. Evidence-only OSINT self-audit: public, keyless,
first-party endpoints only. No credential probes, no breach dumps, no
.onion, no form submission - enforced in code.

## When to use

- "audit my digital footprint" / "what can strangers find about me"
- "check this email/username for breaches and accounts"
- "what does the public web hold on this domain/url/phone"
- privacy, data-broker and opsec questions (the TUI chat answers those)

## How to run

```bash
thousand-eyes <email|username|domain|url|phone>          # polite sweep
thousand-eyes <lead> --full                              # wildfire sweep
thousand-eyes <lead> --json report.json --html report.html
thousand-eyes --agent <lead>                             # LLM-planned audit
thousand-eyes                                            # chat TUI
```

## Reading results

- WEAPONISED tier: identity keys an attacker starts from - fix first.
- ACCOUNTS CONFIRMED: pivot doors, each with evidence URLs - every claim
  is response-confirmed, never guessed.
- ambient footprint: corroborating public traces.
- skipped lines are honest refusals (keyed checks, fence rules), not
  failures. INCONCLUSIVE means "could not verify", never a negative.

## Hard rules (do not work around)

1. Only audit identifiers the user owns or has consent to audit.
2. Never probe logins, never fetch breach dumps, never touch .onion.
3. Report what the evidence says; uncertainty is INCONCLUSIVE.
"""


def skill_paths(scope: str = "claude") -> list[str]:
    home = os.path.expanduser("~")
    paths = [os.path.join(home, ".claude", "skills", SKILL_NAME, "SKILL.md")]
    if scope == "all":
        paths.append(os.path.join(home, ".agents", "skills", SKILL_NAME,
                                  "SKILL.md"))
    return paths


def install_skill(scope: str = "claude") -> list[str]:
    """Write the skill file(s). Returns the paths written."""
    written = []
    for path in skill_paths(scope):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SKILL_MD)
        written.append(path)
    return written


def uninstall_skill() -> list[str]:
    removed = []
    for path in skill_paths("all"):
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def skill_status() -> str:
    for path in skill_paths("claude"):
        if os.path.exists(path):
            return "skill: installed (~/.claude/skills/thousand-eyes)"
    return "skill: not installed (run `thousand-eyes --skill install`)"
