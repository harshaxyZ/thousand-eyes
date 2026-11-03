"""Output writers: default text (the risk summary), --json, and graph.dot.

v0.3 default text is the demo money-shot: the pivot path with per-edge
evidence, the "also registered on" leaves, the mail-infrastructure arm, a
counts summary ("what one email spawns"), curated manual-check links for
human follow-up, then skipped/errors. Zero pivots still prints the
legacy "no public pivot" line and the caller exits 0.
"""

from __future__ import annotations

import json

from thousand_eyes.models import ScanResult

_MANUAL_CHECKS: dict[str, list[str]] = {
    "email": [
        ("Have I Been Pwned (account-level, browser)", "https://haveibeenpwned.com/"),
        ("Epieos Google-account check (browser)", "https://epieos.com/"),
        ("IntelTechniques email lookup CSEs (browser)", "https://inteltechniques.com/tools/"),
        ("Data-broker exposure + opt-outs (browser)", "https://www.inteltechniques.com/remove.html"),
    ],
    "username": [
        ("WhatsMyName web sweep (browser)", "https://whatsmyname.app/"),
        ("Namechk (browser)", "https://namechk.com/"),
        ("PimEyes face search (browser, ethics-sensitive)", "https://pimeyes.com/"),
    ],
    "phone": [
        ("IntelTechniques phone lookup CSEs (browser)", "https://inteltechniques.com/tools/"),
        ("Sync.me / reverse phone (browser)", "https://sync.me/"),
    ],
    "url": [
        ("Wayback UI (browser)", "https://web.archive.org/"),
        ("Shodan InternetDB (browser)", "https://internetdb.shodan.io/"),
    ],
    "domain": [
        ("crt.sh UI (browser)", "https://crt.sh/"),
        ("DNSlytics (browser)", "https://dnslytics.com/"),
    ],
    "name": [
        ("Google dorks (browser)", "https://dorksearch.com/"),
    ],
}


def _summary_counts(result: ScanResult) -> str:
    type_counts: dict[str, int] = {}
    for node in result.nodes.values():
        if node.value != result.seed:
            type_counts[node.type] = type_counts.get(node.type, 0) + 1
    leaves_n = len(result.leaves)
    errors_n = len(result.errors)
    parts = ", ".join(f"{t}: {n}" for t, n in sorted(type_counts.items())) or "no findings"
    return f"pivots by type — {parts} | leaves: {leaves_n} | errors: {errors_n}"


def render_text(result: ScanResult) -> str:
    lines: list[str] = []

    pivot_edges = result.edges
    if not pivot_edges:
        lines.append(f"{result.seed}: no public pivot")
    else:
        lines.append(f"spider-web for {result.seed}:")
        for edge in pivot_edges:
            lines.append(f"  {edge.from_value}  --[{edge.source}]-->  {edge.to_value}")
            lines.append(f"      evidence: {edge.evidence_url}")
            lines.append(f"      matched:  {edge.snippet}")

        lines.append("")
        lines.append(_summary_counts(result))

        seed_types = [t.strip() for t in result.seed_type.split(",") if t.strip()]
        manual: list[tuple[str, str]] = []
        for seed_index, seed_type in enumerate(seed_types):
            for label, url in _MANUAL_CHECKS.get(seed_type, []):
                if (label, url) not in manual:
                    manual.append((label, url))
        if manual:
            lines.append("")
            lines.append("manual human checks next (browser only, not automated):")
            for label, url in manual:
                lines.append(f"  - {label}: {url}")

    if result.leaves:
        lines.append("")
        lines.append("also registered on / infrastructure:")
        for leaf in result.leaves:
            status_part = f" (status {leaf.status})" if leaf.status is not None else ""
            lines.append(f"  {leaf.site}{status_part}")
            lines.append(f"      evidence: {leaf.url}")

    if result.skipped:
        lines.append("")
        lines.append("skipped:")
        for s in result.skipped:
            lines.append(f"  {s.module}: {s.reason}")

    if result.errors:
        lines.append("")
        lines.append("errors (inconclusive — never a fabricated negative):")
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
