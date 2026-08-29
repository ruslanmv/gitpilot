# gitpilot/topology/registry.py
"""The topology registry — Batch V4-G1.

Supersedes :mod:`gitpilot.topology_registry`, which is now a re-exporting shim so
every existing import keeps working. What is new here is that a topology can come
from a **document** as well as from Python:

1. the nine hand-written v1 entries (:mod:`gitpilot.topology.legacy`)
2. built-in v2 documents in ``defaults/*.yaml`` — these *replace* a v1 entry with
   the same id, which is how §15.3's migration lands one topology at a time
3. ``~/.gitpilot/topologies.yaml`` — the user's own machine
4. ``<workspace>/.gitpilot/topologies.yaml`` — **only from a trusted workspace**

That last line is the one worth being awake for. A topology grants capabilities
and can set an approval mode, so a repo-local topology file is executable
configuration shipped by whoever wrote the repo. Cloning a stranger's project and
opening it must not hand that project a say in what GitPilot may do without
asking, so the file is read only when :mod:`gitpilot.trusted_folders` says the
workspace is trusted — and even then, ``approval.mode`` can only *tighten*
(:meth:`~gitpilot.topology.schema.ApprovalSpec.effective_mode`).

Workspace-scoped lookups take a ``workspace`` argument; the module-level
``TOPOLOGY_REGISTRY`` is the workspace-free view (built-ins plus the user file)
and is what the legacy callers see.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..yaml_lite import load_yaml_or_json
from .legacy import LEGACY_ORDER, LEGACY_TOPOLOGIES
from .schema import (  # noqa: F401 — re-exported for API stability
    SUBAGENT_TEMPLATES,
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

logger = logging.getLogger(__name__)

#: The registry's public surface.  Explicit because
#: :mod:`gitpilot.topology_registry` re-exports it as a compatibility shim, and a
#: re-export of a name this module has not declared public is exactly the kind of
#: accidental API a shim should not create.
__all__ = [
    "AUTOMATIC_TOPOLOGY_IDS",
    "BUILTIN_DIR",
    "DEFAULT_TOPOLOGY_ID",
    "DOCUMENTS_KEY",
    "PROJECT_TOPOLOGIES_REL",
    "TOPOLOGY_REGISTRY",
    "USER_TOPOLOGIES_FILE",
    "ApprovalMode",
    "ApprovalSpec",
    "ClassificationResult",
    "DialectChoice",
    "Engine",
    "ExecutionSpec",
    "ExecutionStyle",
    "LimitsSpec",
    "RoutingPolicy",
    "RoutingStrategy",
    "SUBAGENT_TEMPLATES",
    "SchemaError",
    "Topology",
    "TopologyCategory",
    "TopologyMeta",
    "TopologyPolicy",
    "VerificationMode",
    "VerificationSpec",
    "build_flow_graph",
    "builtin_documents",
    "classify_message",
    "clear_topology_preference",
    "get_policy",
    "get_saved_topology_preference",
    "get_topology",
    "get_topology_graph",
    "list_topologies",
    "load_documents",
    "parse_document",
    "project_documents",
    "refresh",
    "registry_for",
    "resolve_id",
    "save_topology_preference",
    "user_documents",
    "workspace_is_trusted",
]

DEFAULT_TOPOLOGY_ID = "default"

#: Built-in v2 documents ship inside the package.
BUILTIN_DIR = Path(__file__).parent / "defaults"

#: The user's own topologies, on their own machine — same standing as
#: ``~/.gitpilot/modes.yaml``.
USER_TOPOLOGIES_FILE = Path.home() / ".gitpilot" / "topologies.yaml"

#: A project's topologies.  Loaded only from a trusted workspace.
PROJECT_TOPOLOGIES_REL = Path(".gitpilot") / "topologies.yaml"

#: The key documents live under, so one file can hold several.
DOCUMENTS_KEY = "topologies"


# ---------------------------------------------------------------------------
# Loading documents
# ---------------------------------------------------------------------------


def load_documents(path: Path, *, source: str) -> List[Topology]:
    """Every topology in one file.

    A document that fails validation is skipped with a warning and the rest of
    the file still loads: one typo in a user's fifth topology should not take out
    the four above it. The skip is loud because the alternative — a topology that
    silently is not there — looks exactly like GitPilot ignoring the file.
    """
    if not path.exists():
        return []
    try:
        data = load_yaml_or_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a bad config file is not a crash
        logger.warning("could not read topology file %s: %s", path, exc)
        return []

    raw_documents: List[Any]
    if isinstance(data, dict) and isinstance(data.get(DOCUMENTS_KEY), list):
        raw_documents = list(data[DOCUMENTS_KEY])
    elif isinstance(data, dict) and data.get("id"):
        raw_documents = [data]          # a single-document file
    else:
        logger.warning(
            "topology file %s has no %r list and no 'id' — ignored", path, DOCUMENTS_KEY,
        )
        return []

    loaded: List[Topology] = []
    for entry in raw_documents:
        try:
            loaded.append(parse_document(entry, source=source))
        except SchemaError as exc:
            logger.warning("ignoring invalid topology in %s: %s", path, exc)
    return loaded


def builtin_documents() -> List[Topology]:
    """The v2 documents shipped with GitPilot, in filename order."""
    if not BUILTIN_DIR.is_dir():
        return []
    loaded: List[Topology] = []
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        loaded.extend(load_documents(path, source="builtin"))
    return loaded


def user_documents() -> List[Topology]:
    return load_documents(USER_TOPOLOGIES_FILE, source="user")


def project_documents(workspace: Optional[Path]) -> List[Topology]:
    """A workspace's own topologies — trusted workspaces only.

    Returns ``[]`` for an untrusted or unknown workspace *without* reading the
    file, so an untrusted clone cannot ship a topology that weakens approvals.
    """
    if workspace is None:
        return []
    path = Path(workspace) / PROJECT_TOPOLOGIES_REL
    if not path.exists():
        return []
    if not workspace_is_trusted(workspace):
        logger.info(
            "ignoring %s: %s is not a trusted workspace. Trust it to load its topologies.",
            path, workspace,
        )
        return []
    return load_documents(path, source="project")


def workspace_is_trusted(workspace: Path) -> bool:
    """Whether :mod:`gitpilot.trusted_folders` vouches for *workspace*."""
    try:
        from ..trusted_folders import TrustStatus, TrustStore

        return TrustStore.default().status(Path(workspace)) is TrustStatus.TRUSTED
    except Exception as exc:  # noqa: BLE001 - no trust store ⇒ not trusted
        logger.debug("could not read the trust store (%s); treating as untrusted", exc)
        return False


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _assemble(*groups: List[Topology]) -> Dict[str, Topology]:
    """Legacy entries first, then each group overlaying by id.

    Order is the migration mechanism: a built-in document named ``code_inspector``
    replaces the v1 ``code_inspector``, and the picker never sees both.

    A document may also *absorb* a v1 topology by listing its id as an alias —
    that is how T2 ``gitpilot_code`` retires into the Autonomous Engineer (§15.3).
    The absorbed id stops being a registry entry and starts being an alias, so a
    saved preference still resolves while the picker shows one card instead of two.
    """
    registry: Dict[str, Topology] = {
        topology_id: LEGACY_TOPOLOGIES[topology_id] for topology_id in LEGACY_ORDER
    }
    for group in groups:
        for topology in group:
            registry[topology.id] = topology

    for topology in list(registry.values()):
        for alias in topology.aliases:
            absorbed = registry.get(alias)
            # Only a v1 entry is absorbed.  A document claiming another
            # *document's* id as an alias is a conflict, not a migration, and
            # deleting the other one would let load order decide which policy a
            # user gets.
            if absorbed is None or absorbed is topology:
                continue
            if absorbed.policy is not None:
                logger.warning(
                    "topology %r claims %r as an alias, but %r is itself a policy "
                    "document; keeping both", topology.id, alias, alias,
                )
                continue
            del registry[alias]
    return registry


def registry_for(workspace: Optional[Path] = None) -> Dict[str, Topology]:
    """The topologies visible from *workspace*.

    Files are re-read on each call rather than cached: a user who edits
    ``topologies.yaml`` expects the next request to see it, and these files are
    small. Trust is likewise re-checked, so revoking trust takes effect at once.
    """
    return _assemble(builtin_documents(), user_documents(), project_documents(workspace))


#: The workspace-free view — built-ins plus the user's own file.  Rebuilt by
#: :func:`refresh`; every legacy caller reads this.
TOPOLOGY_REGISTRY: Dict[str, Topology] = _assemble(builtin_documents(), user_documents())


def refresh() -> Dict[str, Topology]:
    """Re-read the topology files into ``TOPOLOGY_REGISTRY`` in place.

    In place because callers hold a reference to the dict — ``from
    gitpilot.topology_registry import TOPOLOGY_REGISTRY`` binds the object, and
    rebinding the module global would leave them on the old one.
    """
    rebuilt = _assemble(builtin_documents(), user_documents())
    TOPOLOGY_REGISTRY.clear()
    TOPOLOGY_REGISTRY.update(rebuilt)
    return TOPOLOGY_REGISTRY


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _view(workspace: Optional[Path]) -> Dict[str, Topology]:
    """The registry to answer a lookup from.

    ``None`` means the workspace-free view, which is already in memory — so a
    caller that does not care about project topologies pays no file reads.
    """
    return registry_for(workspace) if workspace is not None else TOPOLOGY_REGISTRY


def _resolve_in(registry: Dict[str, Topology], topology_id: str) -> Optional[str]:
    if not topology_id:
        return None
    if topology_id in registry:
        return topology_id
    for candidate_id, topology in registry.items():
        if topology_id in topology.aliases:
            return candidate_id
    return None


def resolve_id(topology_id: str, *, workspace: Optional[Path] = None) -> Optional[str]:
    """The canonical id for *topology_id*, following aliases.

    A saved preference outlives the topology it names (§15.3 retires ids), so an
    alias has to resolve rather than 404.
    """
    return _resolve_in(_view(workspace), topology_id)


def get_topology(topology_id: str, *, workspace: Optional[Path] = None) -> Optional[Topology]:
    """Look up a topology by id or alias.  Returns ``None`` if not found."""
    registry = _view(workspace)
    resolved = _resolve_in(registry, topology_id)
    return registry.get(resolved) if resolved else None


def list_topologies(
    *, workspace: Optional[Path] = None, include_hidden: bool = False,
) -> List[Dict[str, Any]]:
    """Lightweight summaries of the registered topologies."""
    result = []
    for topology in _view(workspace).values():
        if topology.hidden and not include_hidden:
            continue
        meta = topology.to_meta()
        result.append({
            "id": meta.id,
            "name": meta.name,
            "description": meta.description,
            "category": meta.category.value,
            "icon": meta.icon,
            "agents_used": meta.agents_used,
            "execution_style": meta.execution_style.value,
        })
    return result


def get_topology_graph(
    topology_id: Optional[str] = None, *, workspace: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return the flow graph for a given topology.

    If *topology_id* is ``None`` or unrecognised, falls back to ``"default"``.
    The returned dict is the same shape as the legacy ``get_flow_definition()``
    output so the frontend ``FlowViewer`` can consume it without changes.
    """
    registry = _view(workspace)
    resolved = _resolve_in(registry, topology_id or DEFAULT_TOPOLOGY_ID)
    topo = registry.get(resolved) if resolved else None
    if topo is None:
        topo = registry[DEFAULT_TOPOLOGY_ID]

    # Build the response — include topology metadata alongside the graph
    # so the frontend can display the name/description even without a
    # separate metadata call.
    graph = topo.flow_graph.copy()

    # For backward compat with the legacy FlowViewer which expects flat
    # ``nodes`` with ``label``/``type``/``description`` keys, we normalise
    # the new richer node format.  New FlowViewer uses the ``data`` sub-key
    # directly but old code reads top-level keys.
    legacy_nodes = []
    for n in graph.get("nodes", []):
        ln = dict(n)  # shallow copy
        # Hoist data.label / data.description to top level for legacy compat
        d = n.get("data", {})
        ln.setdefault("label", d.get("label", n["id"]))
        ln.setdefault("description", d.get("description", ""))
        legacy_nodes.append(ln)

    return {
        "topology_id": topo.id,
        "topology_name": topo.name,
        "topology_icon": topo.icon,
        "topology_description": topo.description,
        "execution_style": topo.execution_style.value,
        "nodes": legacy_nodes,
        "edges": graph.get("edges", []),
    }


def get_policy(
    topology_id: Optional[str], *, workspace: Optional[Path] = None,
) -> Optional[TopologyPolicy]:
    """The v2 policy document for *topology_id*, or ``None`` for a v1 entry.

    This is what the runner asks: a topology with no document keeps the
    unrestricted Phase C behaviour, so adding documents one at a time cannot
    change a topology that has not been migrated yet.
    """
    topology = get_topology(topology_id or "", workspace=workspace)
    return topology.policy if topology else None


# ===========================================================================
# Topology classifier — keyword-based auto-detection
# ===========================================================================


@dataclass
class ClassificationResult:
    """Result of classifying a user message against topology hints."""

    recommended: str
    confidence: float
    alternatives: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommended_topology": self.recommended,
            "confidence": round(self.confidence, 3),
            "alternatives": self.alternatives,
        }


def classify_message(message: str) -> ClassificationResult:
    """Classify a user message and recommend the best topology.

    Uses a two-pass approach:
      1. Keyword hit scoring against each topology's ``classifier_hints``.
      2. Tie-breaking heuristics (message length, question marks, etc.).

    System topologies (T1, T2) act as fallbacks — they are only recommended
    when no pipeline topology scores above the confidence threshold.
    """
    msg_lower = message.lower().strip()
    scores: List[Tuple[str, float]] = []

    for tid, topo in TOPOLOGY_REGISTRY.items():
        if topo.hidden:
            continue
        hints = topo.routing_policy.classifier_hints
        if not hints:
            continue  # system topologies have no hints

        hit_count = 0
        for hint in hints:
            # Match whole-word (ish) — allow the hint to appear as a substring
            # but prefer word boundaries.
            pattern = r"(?:^|\W)" + re.escape(hint) + r"(?:\W|$)"
            if re.search(pattern, msg_lower):
                hit_count += 1

        if hit_count > 0:
            # Normalise: score = hits / total_hints, capped at 1.0
            raw_score = min(hit_count / max(len(hints), 1), 1.0)
            # Boost by number of distinct hits so more-specific matches win
            boosted = raw_score * (1 + 0.1 * min(hit_count, 5))
            scores.append((tid, min(boosted, 1.0)))

    # Sort descending by score
    scores.sort(key=lambda x: x[1], reverse=True)

    if not scores or scores[0][1] < 0.05:
        # Nothing matched — fall back to default
        return ClassificationResult(
            recommended="default",
            confidence=0.5,
            alternatives=[
                {"id": "gitpilot_code", "confidence": 0.45},
            ],
        )

    best_id, best_score = scores[0]
    alternatives = [
        {"id": tid, "confidence": round(sc, 3)}
        for tid, sc in scores[1:4]
    ]

    # Always include the two system topologies as alternatives if not already present
    present_ids = {best_id} | {a["id"] for a in alternatives}
    for sys_id, sys_conf in [("default", 0.3), ("gitpilot_code", 0.35)]:
        if sys_id not in present_ids:
            alternatives.append({"id": sys_id, "confidence": sys_conf})

    return ClassificationResult(
        recommended=best_id,
        confidence=round(best_score, 3),
        alternatives=alternatives,
    )


# ===========================================================================
# User preference persistence
# ===========================================================================

_TOPOLOGY_PREF_KEY = "active_topology"


def get_saved_topology_preference() -> Optional[str]:
    """Read the user's saved topology preference from settings file."""
    from ..settings import CONFIG_DIR

    pref_file = CONFIG_DIR / "topology_pref.json"
    if pref_file.exists():
        try:
            data = json.loads(pref_file.read_text("utf-8"))
            value = data.get(_TOPOLOGY_PREF_KEY)
            if isinstance(value, str) and resolve_id(value):
                return value
        except Exception:
            pass
    return None


#: Values that mean "no fixed topology — route each request on intent".
AUTOMATIC_TOPOLOGY_IDS = frozenset({"", "auto", "automatic", "none"})


def clear_topology_preference() -> None:
    """Forget any saved topology, restoring per-request routing."""
    from ..settings import CONFIG_DIR

    pref_file = CONFIG_DIR / "topology_pref.json"
    pref_file.unlink(missing_ok=True)


def save_topology_preference(topology_id: str) -> None:
    """Persist the user's selected topology preference.

    "Automatic" is the *absence* of a preference, not a topology, so those
    ids clear the file instead of writing a value no reader recognises.
    An unknown id is rejected outright: silently storing one leaves a
    setting that appears to be saved but does nothing.
    """
    from ..settings import CONFIG_DIR

    normalised = (topology_id or "").strip().lower()
    if normalised in AUTOMATIC_TOPOLOGY_IDS:
        clear_topology_preference()
        return

    if resolve_id(topology_id) is None:
        raise ValueError(
            f"Unknown topology {topology_id!r}. "
            f"Available: {', '.join(sorted(TOPOLOGY_REGISTRY))}"
        )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    pref_file = CONFIG_DIR / "topology_pref.json"
    pref_file.write_text(
        json.dumps({_TOPOLOGY_PREF_KEY: topology_id}, indent=2),
        "utf-8",
    )
