"""Module (conditional interconnect step): keybase-proofs.

Only runs against a GitHub username already confirmed by github_profile.py
(itself only reachable from an already-evidence-backed github.com/<user>
URL pivot) - never a locally-derived guess from the email local-part
(hard rule #2).

Calls Keybase's public, keyless proof-lookup API
(keybase.io/_/api/1.0/user/lookup.json?github=<user>). Keybase proofs are
cryptographically signed by the user's own Keybase key and independently
checkable at each proof's own `proof_url`/`human_url` - this is a
verification mechanism, not a guess: we only report a pivot when Keybase's
own API says a specific proof is in state 1 (accepted/live), and every
pivot's evidence_url is the proof's own checkable URL, never Keybase's
lookup URL itself.

A "no matching Keybase user" response is silence, not an error (hard rule
#7: inconclusive is reported, but a well-formed "this GitHub user has no
Keybase account" is a real negative, not a block/timeout/ambiguity).
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "keybase-proofs"
API_URL = "https://keybase.io/_/api/1.0/user/lookup.json"

# Keybase proof_type -> pivot node type. Only types whose proof_url is an
# independently-fetchable https:// URL on the claimed third-party service
# are listed. "dns" is deliberately excluded: Keybase's API returns that
# proof's proof_url pointing back into keybase.io's own sigchain viewer
# (its service_url is a bare http:// domain, its human_url a dns:// URI
# neither of which is independently checkable), so including it would
# violate our own rule of never using Keybase's own URL as the evidence.
_PROOF_TYPE_TO_NODE = {
    "twitter": "url",
    "github": "url",
    "reddit": "url",
    "hackernews": "url",
    "generic_web_site": "url",
}

# Keybase's documented proof state for a currently-live, checked-good proof.
_PROOF_STATE_LIVE = 1


async def run(client: httpx.AsyncClient, github_username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    try:
        resp = await client.get(
            API_URL,
            params={"github": github_username, "fields": "proofs_summary"},
        )
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}

    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    status = data.get("status") or {}
    if status.get("code") not in (0, None):
        # Keybase's own API reported an application-level error (not found,
        # bad input, etc) -- this is a real, well-formed negative, not an
        # inconclusive block/timeout.
        return {"pivots": [], "error": None}

    # Keybase's lookup API returns `them` as a LIST of candidate matches
    # (confirmed live against api lookups for real accounts), never a bare
    # object -- an empty list, null, or anything that isn't a populated
    # list means no match.
    them = data.get("them")
    if not isinstance(them, list) or not them:
        return {"pivots": [], "error": None}

    first = them[0]
    if not isinstance(first, dict):
        return {"pivots": [], "error": None}

    proofs = ((first.get("proofs_summary") or {}).get("all")) or []

    pivots: list[dict] = []
    for proof in proofs:
        if proof.get("state") != _PROOF_STATE_LIVE:
            continue
        proof_type = proof.get("proof_type")
        node_type = _PROOF_TYPE_TO_NODE.get(proof_type)
        if node_type is None:
            continue

        proof_url = proof.get("proof_url") or proof.get("human_url")
        if not proof_url or not proof_url.startswith("https://"):
            continue

        sig_id = proof.get("sig_id", "")
        nametag = proof.get("nametag", "")
        pivots.append({
            "type": node_type,
            "value": proof_url,
            "source": MODULE,
            "evidence_url": proof_url,
            "snippet": (
                f'Keybase-signed proof: {proof_type} account "{nametag}" '
                f"(sig_id={sig_id})"
            ),
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}
