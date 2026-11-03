from __future__ import annotations

import pytest

from thousand_eyes.normalise import InvalidSeed, normalise_seed


def test_accepts_email():
    seed = normalise_seed("Fixture.User@Example.ORG")
    assert seed.type == "email"
    assert seed.value == "fixture.user@example.org"


def test_fold_plus_only_when_flag_passed():
    seed_default = normalise_seed("fixture+tag@example.org")
    assert seed_default.value == "fixture+tag@example.org"

    seed_folded = normalise_seed("fixture+tag@example.org", fold_plus=True)
    assert seed_folded.value == "fixture@example.org"


def test_accepts_e164_phone():
    seed = normalise_seed("+15551234567")
    assert seed.type == "phone"
    assert seed.value == "+15551234567"


def test_accepts_https_url():
    seed = normalise_seed("https://example.org/fixture")
    assert seed.type == "url"


@ pytest.mark.parametrize(
    "raw",
    [
        "just a name",
        "5551234567",  # all digits -> not E.164, not a plausible username
        "http://example.org/fixture",  # not https
        "",
    ],
)
def test_rejects_anything_else(raw):
    with pytest.raises(InvalidSeed):
        normalise_seed(raw)


def test_accepts_bar_username_seed():
    """v0.3: a bare handle is a USER-ASSERTED lead — the operator typed it,
    which is its evidence — so normalise accepts and labels it."""
    seed = normalise_seed("fixtureuser")
    assert seed.type == "username"
    assert seed.value == "fixtureuser"
