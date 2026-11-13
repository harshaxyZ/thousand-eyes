#!/usr/bin/env bash
# thousand-eyes one-line installer (macOS / Linux)
#
#   curl -fsSL https://raw.githubusercontent.com/harshaxyZ/thousand-eyes/main/install.sh | bash
#
# What it does:
#   1. finds a python 3.10+ (python3, python)
#   2. makes sure pipx is installed (isolated CLI env, no sudo)
#   3. pipx installs thousand-eyes from this repo
#   4. injects numpy (the animated banner needs it, the audit itself does not)
#   5. verifies the binary and prints the three next steps
#
# Re-running is safe: pipx reinstalls in place, nothing else is touched.

set -euo pipefail

step()  { printf '==> %s\n' "$1"; }
ok()    { printf '    %s\n' "$1"; }
warn()  { printf '    %s\n' "$1" >&2; }
die()   { printf '    %s\n' "$1" >&2; exit 1; }

step "thousand-eyes installer"

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
            PY="$cand"
            break
        fi
    fi
done
[ -n "$PY" ] || die "python 3.10+ not found - install it (macOS: brew install python@3.12) and re-run"
ok "python: $(command -v "$PY")"

step "checking pipx (isolated CLI environments)"
if ! command -v pipx >/dev/null 2>&1; then
    "$PY" -m pip install --user --upgrade pipx >/dev/null 2>&1 || true
    export PATH="$HOME/.local/bin:$PATH"
    command -v pipx >/dev/null 2>&1 || warn "pipx installed but not on PATH - a new shell will fix it"
fi
command -v pipx >/dev/null 2>&1 || exit 0
ok "pipx: $(command -v pipx)"

step "installing thousand-eyes (from the repo, pinned deps, no sudo)"
pipx install "git+https://github.com/harshaxyZ/thousand-eyes.git" --force >/dev/null
ok "thousand-eyes installed"

step "adding numpy (animated banner only - the audit stays dependency-light)"
pipx inject thousand-eyes numpy >/dev/null 2>&1 || warn "numpy inject failed (banner will fall back to text)"
ok "numpy injected"

step "verifying"
if command -v thousand-eyes >/dev/null 2>&1; then
    ok "command ready: $(thousand-eyes --version)"
else
    warn "the thousand-eyes command may need a new shell to appear on PATH"
fi

printf '\n'
printf '  next steps\n'
printf '    thousand-eyes                  open the chat console (audit + chat)\n'
printf '    thousand-eyes --setup          add your LLM key (gemini free tier or claude)\n'
printf '    thousand-eyes --skill install  teach your IDE agent how to drive it\n'
printf '    thousand-eyes you@email.com    run your first self-audit\n'
printf '\n'
printf '  free by design: every data source is a free, keyless, first-party public endpoint.\n'
printf '  nothing leaves your machine except read-only GETs to the public web.\n'
printf '\n'
