"""html_report.py - the one-file dossier (v0.4).

Standalone HTML artifact: header, summary stats, the evidence graph drawn
as pure inline SVG (radial layout by impact tier), node tables, leaf tables
and the raw edge list. Zero external assets - no CDN, no fonts, no JS -
so the file is safe to open anywhere and cannot phone home.

Graph classification reuses the CLI's impact ranking.
"""

from __future__ import annotations

import html
import math
from urllib.parse import urlparse

from thousand_eyes import impact
from thousand_eyes.models import Node, ScanResult

_TIER_COLORS = {
    impact.WEAPONISED: "#e5484d",   # red   - weaponised identity keys
    impact.PIVOTABLE: "#b46eff",    # violet- pivotable platform accounts
    impact.AMBIENT: "#8a8f98",      # grey - ambient footprints
}
_TIER_NAMES = {
    impact.WEAPONISED: "weaponised identity keys",
    impact.PIVOTABLE: "pivotable accounts",
    impact.AMBIENT: "ambient footprints",
}
_BG = "#0d0a14"
_FG = "#e9e3ff"
_MID = "#2a2138"


def _esc(v: str) -> str:
    return html.escape(str(v), quote=True)


def _short(value: str, n: int = 64) -> str:
    return value if len(value) <= n else value[: n - 15] + "…" + value[-14:]


def _host(value: str) -> str:
    if "://" in value:
        return urlparse(value).hostname or value
    return value.split("/")[0]


def node_tier(node: Node) -> int:
    if node.type in ("name", "email", "phone"):
        return impact.WEAPONISED
    tier, _ = impact.pivot_tier(node.type, node.value, node.category, node.label)
    return tier


def _radial_layout(result: ScanResult, width: int = 1600, height: int = 900):
    """Seed at center; tier rings: weaponised inner, ambient outer.
    Returns pos dict {(type, value): (x, y, tier)} plus center point."""
    cx, cy = width / 2, height / 2
    rings = {
        impact.WEAPONISED: height * 0.21,
        impact.PIVOTABLE: height * 0.33,
        impact.AMBIENT: height * 0.44,
    }
    buckets: dict[int, list[Node]] = {i: [] for i in (impact.WEAPONISED, impact.PIVOTABLE, impact.AMBIENT)}
    seed_key = None
    for node in result.nodes.values():
        if node.value == result.seed:
            seed_key = node.key()
            continue
        buckets.setdefault(node_tier(node), buckets[impact.AMBIENT]).append(node)

    pos: dict[tuple[str, str], tuple[float, float, int]] = {}
    for tier, here in buckets.items():
        n = max(len(here), 1)
        for i, node in enumerate(here):
            ang = 2 * math.pi * (i + 0.5) / n
            r = rings[tier]
            pos[node.key()] = (cx + r * math.cos(ang), cy + r * math.sin(ang) * 0.92, tier)
    if seed_key:
        pos[seed_key] = (cx, cy, -1)
    return pos, (cx, cy)


def _svg(result: ScanResult) -> str:
    width, height = 1600, 900
    pos, (cx, cy) = _radial_layout(result, width, height)
    index = {k: v for k, v in pos.items()}

    def find(value: str):
        v = value.strip().lower().rstrip("/")
        for (t, k), p in index.items():
            if k.strip().lower().rstrip("/") == v:
                return (t, k)
        return None

    out: list[str] = []
    out.append(f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">')
    out.append(f'<rect width="{width}" height="{height}" fill="{_BG}" rx="18"/>')
    for r in (height * 0.44, height * 0.33, height * 0.21):
        out.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="none" stroke="{_MID}" stroke-dasharray="4 8"/>')

    nodes_by_key = {n.key(): n for n in result.nodes.values()}
    tier_of_pos = {k: p[2] for k, p in pos.items()}

    for edge in result.edges:
        a = find(edge.from_value)
        b = find(edge.to_value)
        if not a or not b:
            continue
        pa, pb = pos.get(a), pos.get(b)
        if not pa or not pb:
            continue
        x1, y1, _ = pa
        x2, y2, _ = pb
        target_tier = tier_of_pos.get(b, impact.AMBIENT)
        color = _TIER_COLORS.get(target_tier, _MID)
        out.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{color}" stroke-opacity="0.35" stroke-width="1.5">'
            f"<title>{_esc(edge.source)}: {_esc(edge.snippet)}</title></line>"
        )

    out.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="10" fill="#ffffff"><title>seed: {_esc(result.seed)}</title></circle>')
    out.append(
        f'<text x="{cx:.0f}" y="{cy - 20:.0f}" text-anchor="middle" fill="{_FG}" '
        f'font-size="15" font-family="monospace">{_esc(_short(result.seed, 44))}</text>'
    )

    for node in result.nodes.values():
        if node.value == result.seed:
            continue
        p = pos.get(node.key())
        if not p:
            continue
        x, y, tier = p
        color = _TIER_COLORS.get(tier, _MID)
        r = 9 if tier == impact.WEAPONISED else 7 if tier == impact.PIVOTABLE else 5
        label = _short(_host(node.value) if node.type == "url" else node.value, 46)
        ly = y - 12 if y < cy else y + 20
        out.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{color}">'
            f"<title>{_esc(node.type)}: {_esc(node.value)}</title></circle>"
        )
        out.append(
            f'<text x="{x:.0f}" y="{ly:.0f}" text-anchor="middle" fill="{_FG}" '
            f'font-size="13" font-family="monospace" opacity="0.92">{_esc(label)}</text>'
        )

    out.append("</svg>")
    return "\n".join(out)


def render_html(result: ScanResult, version: str = "0.4.0") -> str:
    dur = result.meta.get("duration_seconds", "?")
    buckets: dict[int, list[Node]] = {
        impact.WEAPONISED: [],
        impact.PIVOTABLE: [],
        impact.AMBIENT: [],
    }
    for node in result.nodes.values():
        if node.value == result.seed:
            continue
        buckets.setdefault(node_tier(node), buckets[impact.AMBIENT]).append(node)

    sections = []
    for imp in (impact.WEAPONISED, impact.PIVOTABLE, impact.AMBIENT):
        rows = "".join(
            f"<tr>"
            f"<td class='mono tier t{imp}' style='width:70px'>{_esc(n.type)}</td>"
            f"<td><a class='monolink' href='{_esc(n.value if n.type == 'url' else '#')}'>"
            f"{_esc(_short(n.value, 96))}</a></td>"
            f"<td class='dim'>{_esc(n.label or '')}</td></tr>"
            for n in buckets[imp]
        ) or "<tr><td class='dim'>-</td></tr>"
        sections.append(
            f"<h3 style='color:{_TIER_COLORS[imp]}'>■ {_TIER_NAMES[imp]}"
            f"<span class='count'> ({len(buckets[imp])})</span></h3>"
            f"<table>{rows}</table>"
        )

    leaf_rows = "".join(
        f"<tr><td class='cat'>{_esc(l.category or 'leaf')}</td>"
        f"<td class='mono'>{_esc(_short(l.site, 110))}</td>"
        f"<td class='dim'><a href='{_esc(l.url)}'>evidence</a></td></tr>"
        for l in sorted(result.leaves, key=lambda x: (x.category, x.site))
    ) or "<tr><td class='dim'>-</td></tr>"

    edge_rows = "".join(
        "<tr>"
        f"<td class='mono dim'>{_esc(_short(e.from_value, 34))}</td>"
        f"<td class='dim'>{_esc(e.source)}</td>"
        f"<td><a class='mono' href='{_esc(e.evidence_url)}'>{_esc(_short(e.to_value, 56))}</a></td>"
        "</tr>"
        for e in result.edges
    ) or "<tr><td class='dim'>-</td></tr>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>thousand-eyes dossier - {_esc(_short(result.seed, 60))}</title>
<style>
  body {{ background:{_BG}; color:{_FG}; font-family:Segoe UI,system-ui,sans-serif; margin:0; padding:24px 40px; }}
  h1 {{ font-size:22px; letter-spacing:.04em; margin:0 0 2px; }}
  h1 span {{ color:#b46eff; }}
  h3 {{ margin:20px 0 6px; font-size:14px; }}
  .sub {{ color:#8a8f98; font-size:12px; font-family:monospace; margin-bottom:18px; }}
  .count {{ color:#8a8f98; font-weight:400; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th,td {{ text-align:left; padding:4px 12px 4px 0; vertical-align:top; }}
  .mono {{ font-family:Consolas,ui-monospace,monospace; }}
  a {{ color:#b46eff; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .dim {{ color:#8a8f98; }}
  .cat {{ color:#b46eff; width:90px; }}
  .t{impact.WEAPONISED} {{ color:#e5484d; font-weight:700; }}
  .t{impact.PIVOTABLE} {{ color:#b46eff; font-weight:700; }}
  svg {{ width:100%; height:auto; border-radius:12px; margin:8px 0 4px; display:block; }}
  footer {{ margin-top:26px; color:#8a8f98; font-size:11px; }}
</style></head><body>
<h1><span>THOUSAND EYES</span> v{version} - evidence dossier</h1>
<div class="sub">seed: {_esc(result.seed)} &nbsp;·&nbsp; wild: {_esc(str(result.meta.get('wild', False)))} &nbsp;·&nbsp; {dur}s &nbsp;·&nbsp; nodes {len(result.nodes)} · edges {len(result.edges)} · leaves {len(result.leaves)}</div>
{_svg(result)}
{''.join(sections)}
<h3 style='color:#b46eff'>■ leaf records ({len(result.leaves)})</h3>
<table><tr><th>class</th><th>record</th><th>proof</th></tr>{leaf_rows}</table>
<h3 style='color:#8a8f98'>■ evidence edges ({len(result.edges)})</h3>
<table><tr><th>from</th><th>via</th><th>found</th></tr>{edge_rows}</table>
<footer>self-audit evidence snapshot - identifiers owner-consented · graphs are time-sensitive · zero external assets in this file</footer>
</body></html>"""
