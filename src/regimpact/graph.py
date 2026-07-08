"""Builds a NetworkX graph from an Estate for traversal and visualisation."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

import networkx as nx

from .models import Estate, to_column_name

# Maps each entity list on the Estate to its node type label.
_NODE_GROUPS = [
    ("regulations", "Regulation"),
    ("changes", "RegulatoryChange"),
    ("obligations", "Obligation"),
    ("controls", "Control"),
    ("capabilities", "Capability"),
    ("technologies", "Technology"),
    ("evidence", "Evidence"),
    ("systems", "System"),
    ("processes", "BusinessProcess"),
    ("products", "Product"),
    ("data_domains", "DataDomain"),
    ("units", "BusinessUnit"),
    ("risks", "Risk"),
    ("gaps", "Gap"),
    ("remediations", "RemediationAction"),
]


def build_graph(estate: Estate) -> nx.MultiDiGraph:
    """Return a typed directed multigraph of the whole estate."""
    g = nx.MultiDiGraph()
    for attr, node_type in _NODE_GROUPS:
        for entity in getattr(estate, attr):
            data = entity.model_dump()
            label = data.get("name") or data.get("title") or data.get("statement") or entity.id
            attrs = {to_column_name(k): v for k, v in _scalar_only(data).items()}
            g.add_node(entity.id, node_type=node_type, label=str(label)[:80], **attrs)

    for edge in estate.edges:
        g.add_edge(edge.source_id, edge.target_id, key=edge.rel_type.value,
                   rel_type=edge.rel_type.value, weight=edge.weight)
    return g


def _scalar_only(data: dict) -> dict:
    """Keep only GraphML-safe scalar attributes (str/int/float/bool).

    Dates and enums are stringified; None and collection values are dropped.
    """
    out: dict = {}
    for k, v in data.items():
        if v is None or isinstance(v, (list, dict)):
            continue
        if isinstance(v, Enum):
            out[k] = v.value
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
