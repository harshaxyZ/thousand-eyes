---
name: thousand-eyes
description: OSINT self-audit - one lead in (email · username · domain · url · phone), an impact-ranked evidence dossier out. Drives the thousand-eyes TUI; read-only, fenced, evidence-only.
---

# thousand-eyes

Public-data self-audit. The operator types a lead; the TUI sweeps every
confirmed pivot outwards and ranks what an attacker could do with it.

## Two commands

1. install the CLI:    pipx install thousand-eyes
2. install this skill: thousand-eyes skill install

## Bonus

    thousand-eyes-banner --once       # the reactive banner, full animation
    python -m thousand_eyes.banner    # same thing, module form

## Drive the TUI

    python scripts/tui_chat.py "<lead>"            # one lead, one dossier
    python scripts/tui_chat.py "demo.subject@example.org" --demo

The script boots the real TUI in a pty, types the lead exactly as the
operator would, waits for the run, then prints the SUMMARY block (wall
time, LLM calls, tokens, ttft, tools, breaches, pivots).

## Fence (never cross it)

no .onion resolution · no auth probing · no form submission · no password
tests · no breach dumps · per-domain 300ms bucket · global concurrency 8 ·
planner executes, model selects. Blocked == inconclusive, never faked.

## Read-only shell-out

Inside the TUI, prefix any command with `!` - `!git status`,
`!pip show thousand-eyes`. Output lands in the chat pane. Nothing else runs.
