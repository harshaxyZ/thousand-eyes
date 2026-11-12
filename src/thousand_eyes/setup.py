"""First-run onboarding: a guided key setup that just works.

`thousand-eyes --setup` (or /setup in the TUI) walks the operator through
picking a provider (gemini free tier or anthropic) and storing the key in
~/.thousand-eyes/config.json, mode 0600. discover_key() picks that file up
first, so the chat and agent mode come alive immediately after setup.

The chat works fully offline without any key - setup is for the full LLM
streaming experience, never a hard requirement.
"""

from __future__ import annotations

import json
import os
import sys

from thousand_eyes.agent.providers import discover_key

CONFIG_DIR = os.path.expanduser("~/.thousand-eyes")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

PROVIDERS = {
    "gemini": "Google Gemini (free tier, gemini-3.5-flash-lite)",
    "anthropic": "Anthropic Claude (claude-sonnet-5)",
}


def key_status() -> tuple[str, str, str]:
    """(provider, key, source) - source describes where the key came from."""
    provider, key = discover_key()
    if not key:
        return "", "", "none"
    source = "config" if os.path.exists(CONFIG_PATH) else "env/file"
    return provider, key, source


def save_key(provider: str, key: str) -> str:
    """Persist the operator's key to the private config file. Returns path."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    key = key.strip()
    if len(key) < 16:
        raise ValueError("that key looks too short to be real")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump({"provider": provider, "key": key}, fh, indent=1)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return CONFIG_PATH


def clear_key() -> bool:
    """Remove the stored key (operator asked to reset). Returns True if removed."""
    try:
        os.remove(CONFIG_PATH)
        return True
    except OSError:
        return False


def prompt_key() -> int:
    """Interactive terminal onboarding. Returns an exit code."""
    provider, key, source = key_status()
    if key:
        where = CONFIG_PATH if source == "config" else "environment / key file"
        print(f"thousand-eyes: key already configured ({provider}, via {where})")
        choice = input("replace it? [y/N] ").strip().lower()
        if choice not in ("y", "yes"):
            return 0
    print()
    print("pick a provider:")
    for i, (pid, label) in enumerate(PROVIDERS.items(), 1):
        print(f"  {i}) {label}")
    raw = input("choice [1-2]: ").strip()
    provider = {"1": "gemini", "2": "anthropic"}.get(raw, "")
    if not provider:
        print("thousand-eyes: cancelled", file=sys.stderr)
        return 2
    print()
    if provider == "gemini":
        print("free key: https://aistudio.google.com/app/apikey")
    else:
        print("console: https://console.anthropic.com/settings/keys")
    key = input("paste your API key: ").strip()
    try:
        save_key(provider, key)
    except ValueError as exc:
        print(f"thousand-eyes: {exc}", file=sys.stderr)
        return 2
    print(f"stored in {CONFIG_PATH} (mode 0600)")
    print("done - `thousand-eyes` now runs full LLM chat + --agent mode.")
    return 0


def status_line() -> str:
    """One-liner for the TUI /setup display."""
    provider, key, source = key_status()
    if key:
        where = "config file" if source == "config" else "environment"
        return f"llm key: found ({provider}, via {where}) - full chat + --agent active"
    return ("llm key: not set - chat runs on the offline engine "
            "(add one: /setup gemini <key> or `thousand-eyes --setup`)")
