"""Evidence-only data model.

Two node classes only, per spec:
  - pivot: a response contained a NEW identifier (name, handle, url, phone,
    second email, avatar url). Pivots get expanded (fetched) up to max depth.
  - leaf: a response only said "registered" / "not registered". Leaves are
    never searched again.

An edge exists only when "source A returned identifier B" — never because
two strings look similar. Every edge must carry source, evidence_url, and
the exact snippet that justified it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def normalise_value(node_type: str, value: str) -> str:
    """Normalisation used purely for dedup keys — never for inventing links."""
    if node_type in ("email", "domain", "handle"):
        return value.strip().lower()
    if node_type == "url":
        v = value.strip().lower()
        if v.endswith("/"):
            v = v[:-1]
        return v
    return value.strip()


@dataclass
class Node:
    type: str  # email | phone | url | name | handle | domain
    value: str
    pivot: bool  # True = pivot, False = leaf

    def key(self) -> tuple[str, str]:
        return (self.type, normalise_value(self.type, self.value))


@dataclass
class Edge:
    from_value: str
    to_value: str
    source: str
    evidence_url: str
    snippet: str
    status: int | None = None
    def to_dict(self) -> dict:
        return {
            "from": self.from_value,
            "to": self.to_value,
            "source": self.source,
            "evidence_url": self.evidence_url,
            "snippet": self.snippet,
            "status": self.status,
        }


@dataclass
class Leaf:
    site: str
    url: str
    status: int | None = None

    def to_dict(self) -> dict:
        return {"site": self.site, "url": self.url, "status": self.status}


@dataclass
class ModuleError:
    module: str
    error: str

    def to_dict(self) -> dict:
        return {"module": self.module, "error": self.error}


@dataclass
class Skipped:
    module: str
    reason: str

    def to_dict(self) -> dict:
        return {"module": self.module, "reason": self.reason}


@dataclass
class ScanResult:
    seed: str
    seed_type: str
    nodes: dict[tuple[str, str], Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    leaves: list[Leaf] = field(default_factory=list)
    errors: list[ModuleError] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    _edge_seen: set[tuple[str, str, str]] = field(default_factory=set)

    def add_node(self, node: Node) -> Node:
        """Add a node, deduping on (type, normalised value). First write wins
        for the pivot/leaf flag — a node already known as a pivot stays a
        pivot even if later re-observed as a leaf-ish mention."""
        key = node.key()
        existing = self.nodes.get(key)
        if existing is not None:
            if node.pivot and not existing.pivot:
                existing.pivot = True
            return existing
        self.nodes[key] = node
        return node

    def add_edge(self, from_value: str, to_value: str, source: str, evidence_url: str, snippet: str, status: int | None = None) -> bool:
        """One edge per (from, to, source) — the pair is case/dedup-normalised so
        the same discovery from two response variants collapses instead of
        duplicating the graph (bug fix: previously every repeat discovery
        appended a duplicate edge)."""
        key = (from_value.strip().lower(), to_value.strip().lower(), source)
        if key in self._edge_seen:
            return False
        self._edge_seen.add(key)
        self.edges.append(Edge(from_value, to_value, source, evidence_url, snippet, status))
        return True

    def to_dict(self) -> dict:
        connected_values = set()
        for e in self.edges:
            connected_values.add(e.from_value)
            connected_values.add(e.to_value)
        nodes_out = []
        for node in self.nodes.values():
            if node.value == self.seed or node.value in connected_values:
                nodes_out.append({"type": node.type, "value": node.value, "class": "pivot" if node.pivot else "leaf"})
        return {
            "seed": self.seed,
            "nodes": nodes_out,
            "edges": [e.to_dict() for e in self.edges],
            "leaves": [l.to_dict() for l in self.leaves],
            "errors": [e.to_dict() for e in self.errors],
            "skipped": [s.to_dict() for s in self.skipped],
        }
