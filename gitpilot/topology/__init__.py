# gitpilot/topology/__init__.py
"""Topologies — declarative policy documents over one execution engine.

Batch V4-G1 split what used to be a single 1,145-line module into three:

* :mod:`gitpilot.topology.schema` — the v2 document (§15.1) and the v1
  vocabulary the picker and the clients still speak.
* :mod:`gitpilot.topology.legacy` — the nine hand-written v1 topologies,
  unchanged, each retiring as its document lands.
* :mod:`gitpilot.topology.registry` — assembly, lookup, routing, preferences.

``gitpilot.topology_registry`` re-exports the registry so existing imports keep
working. Importing this package is cheap: the schema pulls the agent package in
lazily, inside the methods that need it, so an API import does not drag the
engine along.
"""
from __future__ import annotations

from .schema import (
    NAMESPACE_LABELS,
    SUBAGENT_TEMPLATES,
    AgentSpec,
    ApprovalMode,
    ApprovalSpec,
    DialectChoice,
    Engine,
    ExecutionSpec,
    ExecutionStyle,
    LimitsSpec,
    RoutingPolicy,
    RoutingStrategy,
    SchemaError,
    Topology,
    TopologyCategory,
    TopologyMeta,
    TopologyPolicy,
    VerificationMode,
    VerificationSpec,
    build_flow_graph,
    parse_document,
)

__all__ = [
    "NAMESPACE_LABELS",
    "SUBAGENT_TEMPLATES",
    "AgentSpec",
    "ApprovalMode",
    "ApprovalSpec",
    "DialectChoice",
    "Engine",
    "ExecutionSpec",
    "ExecutionStyle",
    "LimitsSpec",
    "RoutingPolicy",
    "RoutingStrategy",
    "SchemaError",
    "Topology",
    "TopologyCategory",
    "TopologyMeta",
    "TopologyPolicy",
    "VerificationMode",
    "VerificationSpec",
    "build_flow_graph",
    "parse_document",
]
