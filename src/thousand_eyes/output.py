"""Output writers v0.4: impact-ranked findings first, ambient noise last.

The default render answers the hacker-POV question the operator asked:
"what could a stranger do with what this scan proved?" - weaponised
identity keys (name / second email / phone / stealer logs) come first,
pivotable platform accounts next, ambient footprints (archives, mail
infra, darknet index mentions) listed compactly at the end. Every line is
a RESULT; the fetch/explanation machinery lives in --verbose + JSON.

render_text(result)              → ranked report (default)
render_text(result, verbose=True) → full edge dump with evidence+snippets
render_json / render_dot          → machine formats (unchanged contract)
"""

from __future__ import annotations

import json

from thousand_eyes import impact
from thousand_eyes.models import ScanResult

_MANUAL_CHECKS: dict[str, list[str]] = {
    "email": [
        ("Have I Been Pwned (account-level, browser)", "https://haveibeenpwned.com/"),
        ("Epieos Google-account check (browser)", "https://epieos.com/"),
        ("IntelTechniques email lookup CSEs (browser)", "https://inteltechniques.com/tools/"),
    ],
    "username": [
        ("WhatsMyName web sweep (browser)", "https://whatsmyname.app/"),
        ("PimEyes face search (browser, ethics-sensitive)", "https://pimeyes.com/"),
    ],
    "phone": [
        ("IntelTechniques phone lookup CSEs (browser)", "https://inteltechniques.com/tools/"),
        ("Sync.me / reverse phone (browser)", "https://sync.me/"),
    ],
    "url": [
        ("Shodan InternetDB (browser)", "https://internetdb.shodan.io/"),
    ],
    "domain": [
        ("crt.sh UI (browser)", "https://crt.sh/"),
        ("DNSlytics (browser)", "https://dnslytics.com/"),
    ],
}

_GLYPH = {impact.WEAPONISED: "✜", impact.PIVOTABLE: "▦", impact.AMBIENT: "·"}


def _short_url(v: str, n: int = 84) -> str:
    return v if len(v) <= n else v[: n - 1] + "…"


def _classify_nodes(result: ScanResult) -> dict[int, list[tuple[str, str, str]]]:
    """tier → list of (glyph line, label, category)."""
    tiers: dict[int, list[tuple[str, str, str]]] = {t: [] for t in (impact.WEAPONISED, impact.PIVOTABLE, impact.AMBIENT)}
    for node in result.nodes.values():
        if node.value == result.seed:
            continue
        tier, why = impact.pivot_tier(node.type, node.value, node.category, node.label)
        glyph = _GLYPH.get(tier, "·")
        if node.type == "url":
            shown = _short_url(node.value)
        else:
            shown = node.value
        tiers.setdefault(tier, []).append((f"{glyph} {node.type}: {shown}", why, node.category))
    return tiers


def _collect_leaves(result: ScanResult) -> dict[int, list[tuple[str, str, str]]]:
    tiers: dict[int, list[tuple[str, str, str]]] = {t: [] for t in (impact.WEAPONISED, impact.PIVOTABLE, impact.AMBIENT)}
    for leaf in result.leaves:
        tier, why = impact.leaf_tier(leaf.site, leaf.category)
        glyph = "✖" if leaf.category == "breach" or "breach" in leaf.site.lower() else _GLYPH.get(tier, "·")
        tiers.setdefault(tier, []).append((f"{glyph} {leaf.site}", why, leaf.category))
    return tiers


def _summary_counts(result: ScanResult) -> str:
    type_counts: dict[str, int] = {}
    for node in result.nodes.values():
        if node.value != result.seed:
            type_counts[node.type] = type_counts.get(node.type, 0) + 1
    leaves_n = len(result.leaves)
    errors_n = len(result.errors)
    parts = ", ".join(f"{t}: {n}" for t, n in sorted(type_counts.items()))
    if not parts and leaves_n == 0:
        parts = "no findings"
    elif not parts and leaves_n > 0:
        parts = "leaves only"
    dur = result.meta.get("duration_seconds")
    dur_part = f" · {dur}s" if dur is not None else ""
    return f"{parts} | leaves: {leaves_n} | errors: {errors_n}{dur_part}"


def _handle_derived(url: str, handle: str) -> bool:
    """True when the URL's last path segment IS (or is built from) the
    operator-typed handle. That proves the account exists on that platform -
    it does NOT prove the same human runs it."""
    if not handle:
        return False
    tail = url.rstrip("/").rsplit("/", 1)[-1].lower()
    h = handle.lower()
    return (tail == h or tail.startswith(h + ".") or tail.startswith(h + "-")
            or tail.startswith(h + "_") or tail.startswith(h + "%"))


def _seed_handle(result: ScanResult) -> str:
    """The operator-typed handle, when one exists. Used ONLY for the
    handle-reuse honesty tag - never as a new search key (hard rule #2)."""
    st = result.seed_type or ""
    if "username" in st:
        return result.seed
    if "url" in st:
        tail = result.seed.rstrip("/").rsplit("/", 1)[-1]
        return tail if tail and "." not in tail and "@" not in tail else ""
    return ""


def _coverage_line(result: ScanResult) -> str:
    """Quality metric: how many source families answered, failed or were
    skipped. A thin coverage is itself a finding - the operator should know
    the dossier is partial."""
    sources = result.meta.get("sources") or {}
    ok = sum(v.get("ok", 0) for v in sources.values())
    failed = sum(v.get("failed", 0) for v in sources.values())
    skipped = sum(v.get("skipped", 0) for v in sources.values())
    total = ok + failed + skipped
    if not total:
        return ""
    pct = int(round(100 * ok / total))
    thin = "  <- thin coverage: most sources did not answer" if pct < 50 else ""
    return f"source coverage: {ok} answered · {failed} failed · {skipped} skipped ({pct}% of {total} checks){thin}"


def render_text(result: ScanResult, *, verbose: bool = False, version: str = "0.4.0") -> str:
    if verbose:
        return render_verbose(result, version=version)
    lines: list[str] = []

    node_tiers = _classify_nodes(result)
    leaf_tiers = _collect_leaves(result)
    handle = _seed_handle(result)

    if not result.edges and not result.leaves:
        lines.append(f"{result.seed}: ")
        lines.append(f"  no public pivot")
    else:
        lines.append("")
        lines.append(f"  THOUSAND EYES v{version} - the evidence bite")
        lines.append(f"  seed: {result.seed}")
        lines.append("")
        lines.append(f"  ✜  WEAPONISED - identity keys an attacker starts here")
        for ln, _why, _c in node_tiers.get(impact.WEAPONISED, []):
            lines.append(f"    {ln}")
        for ln, _why, _c in leaf_tiers.get(impact.WEAPONISED, []):
            lines.append(f"    {ln}")
        if not node_tiers.get(impact.WEAPONISED) and not leaf_tiers.get(impact.WEAPONISED):
            lines.append("    - none found -")
        lines.append("")
        lines.append(f"  ▦  ACCOUNTS CONFIRMED - pivot doors, with confidence tags")
        reuse_n = 0
        for ln, _why, _c in node_tiers.get(impact.PIVOTABLE, []):
            if handle and _handle_derived(ln.split(": ", 1)[-1], handle):
                reuse_n += 1
                lines.append(f"    {ln}  [handle-reuse - same person unconfirmed]")
            else:
                lines.append(f"    {ln}  [confirmed]")
        for ln, _why, _c in leaf_tiers.get(impact.PIVOTABLE, []):
            lines.append(f"    {ln}")
        if not node_tiers.get(impact.PIVOTABLE) and not leaf_tiers.get(impact.PIVOTABLE):
            lines.append("    - none found -")
        if reuse_n > 1:
            lines.append(
                f"    note: the handle \"{handle}\" appears on {reuse_n} platforms. "
                "same handle != same person - platforms with [handle-reuse] tags "
                "are literally-matching accounts only. corroborate via bio/name/"
                "avatar overlap or cross-links before attributing them to one identity.")
        amb = node_tiers.get(impact.AMBIENT, []) + leaf_tiers.get(impact.AMBIENT, [])
        lines.append("")
        amb_concise = [ln.split(": ", 1)[1] if ": " in ln else ln[2:] for ln, _w, _c in amb]
        head = amb_concise[:8]
        lines.append(f"  ·  ambient footprint ({len(amb_concise)}) - also on record:")
        for ln in head:
            lines.append(f"    · {ln}")
        if len(amb_concise) > 8:
            lines.append(f"    · … and {len(amb_concise) - 8} more (see --json)")
        lines.append("")
        lines.append("  " + _summary_counts(result))
        coverage = _coverage_line(result)
        if coverage:
            lines.append("  " + coverage)

        seed_types = [t.strip() for t in result.seed_type.split(",") if t.strip()]
        manual: list[tuple[str, str]] = []
        for seed_type in seed_types:
            for label, url in _MANUAL_CHECKS.get(seed_type, []):
                if (label, url) not in manual:
                    manual.append((label, url))
        if manual:
            lines.append(f"  manual browser checks worth firing next: {manual[0][1]}" +
                         (f" (+{len(manual) - 1} more in --verbose)" if len(manual) > 1 else ""))

    if result.skipped:
        lines.append("")
        for s in result.skipped:
            lines.append(f"  skipped: {s.module}: {s.reason}")

    if result.errors:
        lines.append("")
        lines.append(f"  checks that failed ({len(result.errors)}) - inconclusive, never a fabricated negative:")
        for e in result.errors[:4]:
            lines.append(f"    · {e.module}: {e.error}")
        if len(result.errors) > 4:
            lines.append(f"    · … and {len(result.errors) - 4} more (see --verbose)")
    return "\n".join(lines)


def render_verbose(result: ScanResult, *, version: str = "0.4.0") -> str:
    lines: list[str] = []
    lines.append(f"thousand-eyes v{version} - full evidence dump")
    lines.append(_summary_counts(result))
    lines.append("")

    if not result.edges and not result.leaves:
        lines.append(f"{result.seed}: no public pivot (zero confirmed edges, zero leaves)")
        render_no_pivot = True
    else:
        render_no_pivot = False
        lines.append("spider-web:")
        for edge in result.edges:
            lines.append(f"  {edge.from_value}  --[{edge.source}]-->  {edge.to_value}")
            lines.append(f"      evidence: {edge.evidence_url}")
            lines.append(f"      matched:  {edge.snippet}")

        lines.append("")
        lines.append(_summary_counts(result))

        if result.leaves:
            lines.append("")
            lines.append("also registered on / infrastructure / darknet:")
            for leaf in result.leaves:
                status_part = f" (status {leaf.status})" if leaf.status is not None else ""
                cat_part = f"[{leaf.category}] " if leaf.category else ""
                lines.append(f"  {cat_part}{leaf.site}{status_part}")
                lines.append(f"      evidence: {leaf.url}")

    seed_types = [t.strip() for t in result.seed_type.split(",") if t.strip()]
    manual: list[tuple[str, str]] = []
    for seed_type in seed_types:
        for label, url in _MANUAL_CHECKS.get(seed_type, []):
            if (label, url) not in manual:
                manual.append((label, url))
    if manual and not render_no_pivot:
        lines.append("")
        lines.append("manual human checks next (browser only, not automated):")
        for label, url in manual:
            lines.append(f"  - {label}: {url}")

    if result.skipped:
        lines.append("")
        lines.append("skipped:")
        for s in result.skipped:
            lines.append(f"  {s.module}: {s.reason}")

    if result.errors:
        lines.append("")
        lines.append("errors (inconclusive - never a fabricated negative):")
        for e in result.errors:
            lines.append(f"  {e.module}: {e.error}")
    return "\n".join(lines)


def render_json(result: ScanResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_dot(result: ScanResult) -> str:
    data = result.to_dict()
    lines = ["digraph thousand_eyes {", '  rankdir="LR";']

    for node in data["nodes"]:
        shape = "box" if node["class"] == "pivot" else "ellipse"
        label = _dot_escape(f"{node['type']}: {node['value']}")
        node_id = _dot_escape(node["value"])
        lines.append(f'  "{node_id}" [label="{label}", shape={shape}];')

    for edge in data["edges"]:
        from_id = _dot_escape(edge["from"])
        to_id = _dot_escape(edge["to"])
        label = _dot_escape(edge["source"])
        lines.append(f'  "{from_id}" -> "{to_id}" [label="{label}"];')

    lines.append("}")
    return "\n".join(lines)
