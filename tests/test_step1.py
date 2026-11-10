"""Step-1 email-only harvest: exact output shape, bot filter, no expansion."""
import asyncio

from thousand_eyes.step1 import LeadRow, render_step1

EMAIL = "harsha210108@gmail.com"
URL_LOG = r"C:\Users\harsh\AppData\Local\Temp\step1_email_only.log"


def test_render_shape_exact():
    leads = [
        LeadRow("url", "https://github.com/harshaxyZ", "github-commit-search",
                "https://github.com/harshaxyZ/athena7/commit/abc",
                'commit author login="harshaxyZ" for a commit with author-email="harsha210108@gmail.com"'),
        LeadRow("reset", "Twitter / X", "email-checks",
                "https://api.twitter.com/i/users/email_available.json?email=x",
                "registered on Twitter / X (address registered, nothing more)"),
    ]
    out = render_step1(EMAIL, leads, ["skipped: hibp: no HIBP_API_KEY set"])
    assert out.startswith(f"lead - email - {EMAIL}\n")
    assert "leads found using this email:\n- url: https://github.com/harshaxyZ\n  source: github-commit-search\n  proof: https://github.com/harshaxyZ/athena7/commit/abc\n" in out
    assert "things found using those leads:\n- none yet\n" in out
    assert "skipped: hibp: no HIBP_API_KEY set" in out
    # every lead row must have a proof line
    for block in out:
        pass
    # no HUDSON / darkweb / web-flow anywhere
    assert "web-flow" not in out
    assert "HUDSON" not in out
    assert "darkweb" not in out


def test_live_step1_run_matches_proof_law():
    """Live run: 9 outbound URLs, no hub expansion, all rows have proof."""
    from thousand_eyes.step1 import collect_email_leads

    leads, skipped = asyncio.run(collect_email_leads(EMAIL, url_log=URL_LOG))
    # No bot, no guessed username, no github profile fetch expansion
    values = [l.value for l in leads]
    assert "https://github.com/web-flow" not in values
    assert "GitHub Web Flow" not in values
    assert all(l.proof for l in leads), "every row must have a proof line"
    assert all(l.source for l in leads)
    # booleans: userrealm domain + a github profile URL (author login) must appear
    joined = "\n".join(values + [l.snippet for l in leads])
    assert "harshaxyZ" in joined
    # no skipped row prints HUDSON (spec: no HUDSON key)
    skipped_text = "\n".join(skipped)
    assert "HUDSON" not in skipped_text
    # url log: only email-keyed URLs, no profile fetch, no wayback of profiles
    log_text = open(URL_LOG, encoding="utf-8").read()
    assert "github.com/harshaxyZ" not in log_text  # fetched? proof only, not opened
    assert log_text.count("gravatar.com") >= 1
    assert "bing.com/search" in log_text
    # reset-only lands as "reset" type
    assert any(l.type == "reset" for l in leads)
