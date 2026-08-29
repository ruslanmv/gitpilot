# gitpilot/topology_registry.py
"""Compatibility shim — the registry moved to :mod:`gitpilot.topology.registry`.

Batch V4-G1 split this module into a schema, the legacy definitions and the
registry itself. Every name this module ever exported is re-exported here, bound
to the same objects, so existing imports — including ``from
gitpilot.topology_registry import TOPOLOGY_REGISTRY`` and the API layer's aliased
imports — keep working unchanged.

New code should import from :mod:`gitpilot.topology`.
"""
from __future__ import annotations

from .topology.registry import (  # noqa: F401
    AUTOMATIC_TOPOLOGY_IDS,
    DEFAULT_TOPOLOGY_ID,
    TOPOLOGY_REGISTRY,
    ClassificationResult,
    ExecutionStyle,
    RoutingPolicy,
    RoutingStrategy,
    Topology,
    TopologyCategory,
    TopologyMeta,
    TopologyPolicy,
    classify_message,
    clear_topology_preference,
    get_policy,
    get_saved_topology_preference,
    get_topology,
    get_topology_graph,
    list_topologies,
    refresh,
    registry_for,
    resolve_id,
    save_topology_preference,
)

__all__ = [
    "AUTOMATIC_TOPOLOGY_IDS",
    "DEFAULT_TOPOLOGY_ID",
    "TOPOLOGY_REGISTRY",
    "ClassificationResult",
    "ExecutionStyle",
    "RoutingPolicy",
    "RoutingStrategy",
    "Topology",
    "TopologyCategory",
    "TopologyMeta",
    "TopologyPolicy",
    "classify_message",
    "clear_topology_preference",
    "get_policy",
    "get_saved_topology_preference",
    "get_topology",
    "get_topology_graph",
    "list_topologies",
    "refresh",
    "registry_for",
    "resolve_id",
    "save_topology_preference",
]
