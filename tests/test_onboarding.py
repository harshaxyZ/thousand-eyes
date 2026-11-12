"""Onboarding tests: key setup, IDE skill install, CLI flags, TUI commands,
and the identity-honesty rules in the report."""

from __future__ import annotations

import json
import os

import pytest

from thousand_eyes.models import Node, ScanResult
from thousand_eyes.output import render_text


# ------------------------------------------------------------------ setup

def test_save_key_writes_config_and_discover_key_picks_it_up(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    from thousand_eyes import setup
    from thousand_eyes.agent.providers import discover_key

    path = setup.save_key("gemini", "test-key-abcdefghijklmnop")
    assert os.path.exists(path)
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["provider"] == "gemini"
    assert data["key"] == "test-key-abcdefghijklmnop"
    # the provider layer now sees it without any env var
    provider, key = discover_key()
    assert provider == "gemini"
    assert key == "test-key-abcdefghijklmnop"


def test_save_key_rejects_short_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from thousand_eyes import setup
    with pytest.raises(ValueError):
        setup.save_key("gemini", "tooshort")
    with pytest.raises(ValueError):
        setup.save_key("not-a-provider", "test-key-abcdefghijklmnop")


def test_status_line_mentions_setup_when_no_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_KEY", raising=False)
    from thousand_eyes import setup
    line = setup.status_line()
    assert "not set" in line
    assert "--setup" in line


# ------------------------------------------------------------------ skill

def test_install_skill_writes_skill_md(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from thousand_eyes import skill
    written = skill.install_skill()
    assert written
    path = written[0]
    assert path.endswith(os.path.join("thousand-eyes", "SKILL.md"))
    body = open(path, encoding="utf-8").read()
    assert body.startswith("---")
    assert "name: thousand-eyes" in body
    assert "thousand-eyes" in body
    assert "INCONCLUSIVE" in body  # the never-fabricate rule travels with the skill
    assert "installed" in skill.skill_status()
    skill.uninstall_skill()
    assert "not installed" in skill.skill_status()


# ------------------------------------------------------------------ cli

def test_cli_skill_status_flag_exits_zero(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from thousand_eyes import cli
    assert cli.main(["--skill", "status"]) == 0
    out = capsys.readouterr().out
    assert "thousand-eyes" in out


def test_cli_setup_flag_uses_the_guided_flow(monkeypatch):
    from thousand_eyes import cli
    called = {}

    def fake_prompt():
        called["ran"] = True
        return 0

    monkeypatch.setattr("thousand_eyes.setup.prompt_key", fake_prompt)
    assert cli.main(["--setup"]) == 0
    assert called.get("ran") is True


# --------------------------------------------------------- free llm paths

def test_local_ollama_is_discovered_when_running(monkeypatch):
    """A local ollama server = a completely free, keyless, offline LLM."""
    from thousand_eyes.agent import providers

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}]}

    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: FakeResponse())
    assert providers.local_models() == ["qwen2.5:7b"]
    assert providers.best_local_model() == "qwen2.5:7b"


def test_local_ollama_absent_is_not_an_error(monkeypatch):
    from thousand_eyes.agent import providers

    def boom(*a, **k):
        raise providers.httpx.ConnectError("refused")

    monkeypatch.setattr(providers.httpx, "get", boom)
    assert providers.local_models() == []
    assert providers.best_local_model() == ""


def test_gemini_free_tier_403_explains_the_fix(monkeypatch):
    """The 403 google returns when a project has no free-tier access must
    tell the operator what to do, not just print a code."""
    import asyncio

    from thousand_eyes.agent import providers

    class FakeStream:
        status_code = 403

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            yield ""

        async def aread(self):
            return (b'{"error":{"code":403,"message":"The Gemini API free '
                    b'tier is not available for this project."}}')

    class FakeClient:
        def stream(self, *a, **k):
            return FakeStream()

    monkeypatch.setattr(providers.httpx, "AsyncClient",
                        lambda *a, **k: FakeClient())
    client = providers.GeminiClient("fake-key")

    async def _run():
        out = []
        async for chunk in client.chat([{"role": "user", "content": "hi"}]):
            out.append(chunk)
        return out

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(_run())
    assert "free-tier" in str(exc.value)
    assert "ollama" in str(exc.value)


# ------------------------------------------------------------------ doctor
def test_doctor_runs_and_reports_every_section(capsys, monkeypatch):
    from thousand_eyes import doctor

    async def fake_probe(client, name, url):
        return True, "status 200 (1ms)"

    monkeypatch.setattr(doctor, "_probe", fake_probe)
    assert doctor.run_doctor() == 0
    out = capsys.readouterr().out
    for section in ("runtime", "install", "intelligence", "network"):
        assert section in out
    assert "source" not in out  # sanity: the doctor is not a scan
    assert "ready" in out


def test_cli_doctor_flag_exits_zero(monkeypatch):
    from thousand_eyes import cli, doctor

    async def fake_probe(client, name, url):
        return False, "status 500 (1ms)"

    monkeypatch.setattr(doctor, "_probe", fake_probe)
    assert cli.main(["--doctor"]) == 0


# ------------------------------------------------------------------ report

def _username_result() -> ScanResult:
    r = ScanResult(seed="fixturehandle", seed_type="username")
    for url in ("https://github.com/fixturehandle",
                "https://bsky.app/profile/fixturehandle.bsky.social",
                "https://github.com/someoneelse"):
        r.add_node(Node("url", url, pivot=True))
        r.add_edge(from_value="fixturehandle", to_value=url, source="wmn",
                   evidence_url=url, snippet=f"fixture {url}")
    return r


def test_same_handle_across_platforms_is_tagged_handle_reuse():
    text = render_text(_username_result(), version="0.6.2")
    assert "[handle-reuse - same person unconfirmed]" in text
    assert "same handle != same person" in text
    assert "[confirmed]" in text   # the non-matching profile stays confirmed


def test_failed_checks_are_listed_with_reasons():
    r = _username_result()
    r.errors.append(type("E", (), {"module": "hibp", "error": "rate limited (status 429)"})())
    text = render_text(r, version="0.6.2")
    assert "checks that failed (1)" in text
    assert "hibp: rate limited (status 429)" in text


def test_source_coverage_is_reported():
    r = _username_result()
    r.meta["sources"] = {
        "github-profile": {"ok": 2, "failed": 0, "skipped": 0},
        "wayback": {"ok": 0, "failed": 1, "skipped": 0},
        "hibp": {"ok": 0, "failed": 0, "skipped": 1},
    }
    text = render_text(r, version="0.6.2")
    assert "source coverage: 2 answered" in text
    assert "1 failed" in text and "1 skipped" in text
    assert "50% of 4 checks" in text


# ------------------------------------------------------------------ tui

@pytest.mark.asyncio
async def test_tui_setup_and_skill_commands(tmp_path, monkeypatch):
    # keep the test off the real home dir: /skill writes a file
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from thousand_eyes.tui import ThousandEyesApp
    app = ThousandEyesApp()
    async with app.run_test() as pilot:
        said: list[str] = []
        app._say = said.append          # capture agent output
        app._command("/setup")
        await pilot.pause()
        assert any("llm key" in s for s in said)
        said.clear()
        app._command("/skill")
        await pilot.pause()
        assert any("skill written" in s for s in said)
        written = list(tmp_path.rglob("SKILL.md"))
        assert written and "thousand-eyes" in written[0].read_text(encoding="utf-8")
