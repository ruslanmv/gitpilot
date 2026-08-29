# gitpilot/agent/policy.py
"""The PolicyEngine — Batch V4-D1.

One pipeline, invoked by the loop for **every** tool call, reads (§8):

.. code-block:: text

    ToolCall
      │ 1. capability mask        (topology ∩ mode ∩ session)   → deny if absent
      │ 2. path policy            (blocked_paths; edit-guard fileRegex)
      │ 3. command classification (terminal.run / git.*)        → risk elevation
      │ 4. static rules           (plan-mode read-only; denylist)
      │ 5. decision               allow | ask | deny (+ reason, + risk)
      ▼
    Decision {verdict, reason, risk, requires_checkpoint}

Two things make this more than a rules table.

**It is the first caller of code that has always been dead.**
:class:`~gitpilot.permissions.PermissionManager` has been constructed once at
module scope in ``_api_app`` and never asked a question; ``ToolPolicy`` had no
caller at all outside its own tests. Both are consulted here — the manager for
paths and per-action confirmation, the policy for categories and guards — which
is why F5's snake_case mismatch disappears rather than being patched: canonical
tool ids *are* snake_case, so ``ToolPolicy`` sees names of the shape it always
expected.

**Escalation is asymmetric.** The mode lives on the session record and nowhere
else (F11). A request body may *restrict* the session's mode for one request and
may never raise it, because a chat body is not an authenticated channel. Getting
this backwards would reopen F7 through a different door.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ..permissions import Action, PermissionManager, PermissionMode, PermissionPolicy
from ..tool_groups import ToolCategory, ToolPolicy
from ..toolkit.registry import Effect, Risk, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

FLAG_AGENT_POLICY = "agent_policy"

#: Verdicts, in order of severity.  ``max`` over this ordering is how the
#: pipeline escalates without ever de-escalating.
VERDICT_ALLOW = "allow"
VERDICT_ASK = "ask"
VERDICT_DENY = "deny"
_VERDICT_RANK = {VERDICT_ALLOW: 0, VERDICT_ASK: 1, VERDICT_DENY: 2}

_RISK_RANK = {Risk.SAFE: 0, Risk.APPROVAL: 1, Risk.HIGH_RISK: 2}

#: The literal a capability may carry instead of a mapping to force an approval
#: regardless of the tool's declared risk (§8.2's ``github.pr.create: ask``).
QUALIFIER_ASK = "ask"

#: Effects that mean "this changed something a user would want back".
MUTATING_EFFECTS = frozenset({
    Effect.WRITES_FS, Effect.GIT_LOCAL, Effect.GIT_REMOTE, Effect.FORGE_WRITE,
    Effect.EXTERNAL_WRITE,
})

#: Effects a read-only run may keep.  Deliberately not "everything but writes":
#: ``EXECUTES`` is excluded because a command can write without us knowing.
#:
#: The read-only gate asks :func:`_is_mutating` rather than "are the effects a
#: subset of these?", because a tool with *no* declared effects is neither
#: mutating nor provably read-only. ``todo.write`` and ``agent.delegate`` change
#: the run's own state and nothing outside it, so they declare none — and the
#: subset test denied both, making a checklist and a subagent impossible in
#: exactly the read-only runs they are most useful in.
READ_ONLY_EFFECTS = frozenset({Effect.READS_FS})

#: Command classes a read-only run may execute unconditionally — Batch V4-G3.
#:
#: ``_is_mutating`` treats ``EXECUTES`` as mutating because a command can write
#: without us knowing. That is the right default *before* classification and the
#: wrong answer after it: by the time the read-only gate runs, the classifier has
#: already read the command line against an allowlist of binaries — the same
#: evidence that makes ``fs.read`` safe. There is no coherent position where
#: ``fs.read`` is permitted in plan mode and ``cat`` is not, and the version that
#: refused both meant a read-only reviewer could not run ``git status``.
READ_ONLY_COMMAND_CLASSES = frozenset({"READ_ONLY"})

#: Classes a read-only run may execute **only when the capability names them**
#: (``terminal.run: {classes: [READ_ONLY, TEST]}``, §15.3 T5).
#:
#: ``TEST`` sits here rather than above because a suite run executes arbitrary
#: project code and writes caches. A reviewer that cannot answer "does the suite
#: pass?" is a weak reviewer, so the door exists — but a topology has to open it
#: deliberately, in a file someone reviewed.
OPT_IN_READ_ONLY_CLASSES = frozenset({"TEST"})

#: Tools that execute but whose class is knowable without a command line, so the
#: read-only gate can reason about them the same way.  ``test.run`` builds its own
#: command from the detected framework; ``which``/``env`` cannot do anything else.
_STATIC_COMMAND_CLASSES: Mapping[str, str] = {
    "test.run": "TEST",
    "terminal.which": "READ_ONLY",
    "terminal.env": "READ_ONLY",
}


#: Tool id → the :class:`~gitpilot.permissions.Action` it corresponds to, so the
#: file/API representation of the policy (``.gitpilot/permissions.json``) governs
#: canonical tools without restating itself.
_TOOL_ACTIONS: Mapping[str, Action] = {
    "fs.read": Action.READ_FILE,
    "fs.list": Action.READ_FILE,
    "fs.glob": Action.READ_FILE,
    "fs.grep": Action.READ_FILE,
    "fs.write": Action.WRITE_FILE,
    "fs.edit": Action.WRITE_FILE,
    "fs.delete": Action.DELETE_FILE,
    "terminal.run": Action.RUN_COMMAND,
    "terminal.run_snippet": Action.RUN_COMMAND,
    "test.run": Action.RUN_COMMAND,
    "git.commit": Action.GIT_COMMIT,
    "git.push": Action.GIT_PUSH,
    "github.issue.create": Action.CREATE_ISSUE,
    "github.pr.create": Action.CREATE_PR,
}

#: ``ToolCategory`` → the canonical namespaces it covers, so a mode written as
#: ``groups: [read, {edit: {fileRegex: ...}}]`` restricts canonical tools.
_CATEGORY_TOOLS: Mapping[ToolCategory, Tuple[str, ...]] = {
    ToolCategory.READ: ("fs.read", "fs.list", "fs.glob", "fs.grep", "git.read",
                        "github.read", "test.detect"),
    ToolCategory.EDIT: ("fs.write", "fs.edit", "fs.delete"),
    ToolCategory.COMMAND: ("terminal.", "test.run", "git.branch", "git.commit",
                           "git.stash", "git.push"),
    ToolCategory.BROWSER: ("web.",),
    ToolCategory.MCP: ("mcp.",),
    ToolCategory.MODE: ("mode.",),
}

#: Argument names that name a filesystem path, checked against `blocked_paths`.
_PATH_ARGS = ("path", "file", "filename", "target", "dest", "destination")


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """What the engine concluded about one call.

    Same shape the loop has consumed since Batch V4-C2 — that was the point of
    defining it there: this batch replaces a body, not a call graph.
    """

    verdict: str = VERDICT_ALLOW
    reason: str = ""
    risk: str = Risk.SAFE.value
    requires_checkpoint: bool = False
    #: The command class, when one was computed.  Shown on the approval card so
    #: the user is told *what kind of thing* they are approving.
    command_class: str = ""

    @property
    def denied(self) -> bool:
        return self.verdict == VERDICT_DENY

    def escalated_to(
        self,
        verdict: str,
        reason: str,
        *,
        risk: Optional[str] = None,
        command_class: str = "",
    ) -> "Decision":
        """Return this decision raised to *verdict* — never lowered.

        The one-way ratchet §8.3 requires: a stage may make a call more
        restrictive, so a crafted command string cannot argue its way down past
        a stage that already objected.
        """
        keep_verdict = _VERDICT_RANK[verdict] > _VERDICT_RANK[self.verdict]
        new_risk = self.risk
        if risk is not None and _RISK_RANK.get(Risk(risk), 0) > _RISK_RANK.get(Risk(self.risk), 0):
            new_risk = risk
        return replace(
            self,
            verdict=verdict if keep_verdict else self.verdict,
            reason=reason if keep_verdict or not self.reason else self.reason,
            risk=new_risk,
            command_class=command_class or self.command_class,
        )


# ---------------------------------------------------------------------------
# Capability mask
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Qualifier:
    """Conditions attached to a granted capability (§8.2).

    ``paths``/``exclude`` carry a mode's ``fileRegex`` intent as globs;
    ``network`` gates ``terminal.run``; ``max_depth`` bounds ``agent.delegate``;
    ``classes`` restricts which :mod:`~gitpilot.agent.command_class` verdicts a
    shell capability accepts; ``force_ask`` is the literal ``ask`` value.
    """

    paths: Tuple[str, ...] = ()
    exclude: Tuple[str, ...] = ()
    network: Optional[bool] = None
    max_depth: Optional[int] = None
    #: Command classes this capability may run.  Empty means "any that the
    #: classifier would otherwise allow".  A read-only inspection topology says
    #: ``{classes: [READ_ONLY, TEST]}`` and gets a *denial* for a build or an
    #: install rather than an approval prompt nobody should be asked (§15.3, T5).
    classes: Tuple[str, ...] = ()
    force_ask: bool = False

    @classmethod
    def parse(cls, value: Any) -> Optional["Qualifier"]:
        """Read one YAML/JSON capability value, or ``None`` when not granted."""
        if value is False or value is None:
            return None
        if value is True:
            return cls()
        if isinstance(value, str):
            return cls(force_ask=True) if value.strip().lower() == QUALIFIER_ASK else cls()
        if isinstance(value, Mapping):
            paths = _as_tuple(value.get("paths"))
            return cls(
                paths=paths,
                exclude=_as_tuple(value.get("exclude")),
                network=value.get("network") if isinstance(value.get("network"), bool) else None,
                max_depth=(
                    int(value["max_depth"]) if isinstance(value.get("max_depth"), int) else None
                ),
                classes=tuple(
                    item.strip().upper() for item in _as_tuple(value.get("classes"))
                ),
                force_ask=str(value.get("verdict", "")).lower() == QUALIFIER_ASK,
            )
        return cls()

    def permits_class(self, command_class: str) -> bool:
        return not self.classes or command_class.upper() in self.classes

    def permits_path(self, path: str) -> bool:
        if any(fnmatch.fnmatch(path, pattern) for pattern in self.exclude):
            return False
        if not self.paths:
            return True
        return any(_glob_matches_path(path, pattern) for pattern in self.paths)


def _as_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


def _glob_matches_path(path: str, pattern: str) -> bool:
    """``src/**`` should match ``src/a.py``, which bare fnmatch does not do."""
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix.rstrip("/") + "/")
    if pattern.endswith("**"):
        return path.startswith(pattern[:-2])
    return False


@dataclass
class CapabilityMask:
    """The capabilities a run may use, with their qualifiers.

    Satisfies the registry's ``CapabilitySet`` protocol structurally, so
    ``registry.render()`` withholds an ungranted tool's schema entirely — the
    model is not offered a tool it would only be denied (§8.2).
    """

    granted: Dict[str, Qualifier] = field(default_factory=dict)
    #: ``True`` grants anything not explicitly listed.  The permissive default
    #: keeps a topology that has declared nothing working as it does today.
    open_by_default: bool = True
    #: Capabilities named with a falsy value.  Kept separately from "absent"
    #: because an explicit ``fs.delete: false`` must withhold the tool even when
    #: ``fs.*`` grants the family.
    denied: Tuple[str, ...] = ()

    # -- CapabilitySet -----------------------------------------------------

    def allows(self, capability: str) -> bool:
        return self.qualifier(capability) is not None

    def qualifier(self, capability: str) -> Optional[Qualifier]:
        """The qualifier governing *capability*, or ``None`` if not granted.

        Longest-prefix wins, so ``fs.*`` can grant the family while
        ``fs.delete: false`` withholds one member.
        """
        # Explicit denials first: `{fs.*: true, fs.delete: false}` must withhold
        # fs.delete, and checking the wildcard first would grant it.
        for key in self.denied:
            stem = key[:-1].rstrip(".") if key.endswith("*") else key
            if capability == stem or capability.startswith(stem + "."):
                return None

        if capability in self.granted:
            return self.granted[capability]

        best: Optional[Tuple[int, Qualifier]] = None
        for key, qualifier in self.granted.items():
            if not key.endswith("*"):
                continue
            stem = key[:-1].rstrip(".")
            if capability == stem or capability.startswith(stem + "."):
                if best is None or len(stem) > best[0]:
                    best = (len(stem), qualifier)
        if best is not None:
            return best[1]

        return Qualifier() if self.open_by_default else None

    # -- construction ------------------------------------------------------

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "CapabilityMask":
        """Build from the §8.2 YAML shape.

        A mapping that lists anything is closed by default: naming capabilities
        is how a topology says "only these".
        """
        granted: Dict[str, Qualifier] = {}
        denials: list[str] = []
        for key, value in mapping.items():
            qualifier = Qualifier.parse(value)
            if qualifier is None:
                denials.append(key)
            else:
                granted[key] = qualifier
        return cls(granted=granted, open_by_default=False, denied=tuple(denials))

    @classmethod
    def permissive(cls) -> "CapabilityMask":
        return cls(open_by_default=True)

    def intersect(self, other: "CapabilityMask") -> "CapabilityMask":
        """``topology ∩ mode ∩ session`` (§8.2).

        A capability survives only if both sides permit it, and the surviving
        qualifier is the stricter of the two — intersecting must never be a way
        to widen a grant.
        """
        keys = set(self.granted) | set(other.granted)
        granted: Dict[str, Qualifier] = {}
        for key in keys:
            mine, theirs = self.qualifier(key), other.qualifier(key)
            if mine is None or theirs is None:
                continue
            granted[key] = _stricter(mine, theirs)

        return CapabilityMask(
            granted=granted,
            open_by_default=self.open_by_default and other.open_by_default,
            denied=tuple({*self.denied, *other.denied}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open_by_default": self.open_by_default,
            "granted": sorted(self.granted),
            "denied": sorted(self.denied),
        }


def _stricter(a: Qualifier, b: Qualifier) -> Qualifier:
    return Qualifier(
        paths=tuple({*a.paths, *b.paths}) if a.paths and b.paths else (a.paths or b.paths),
        exclude=tuple({*a.exclude, *b.exclude}),
        network=False if (a.network is False or b.network is False) else (
            a.network if a.network is not None else b.network
        ),
        max_depth=_shallower(a.max_depth, b.max_depth),
        # Empty means "unrestricted", so intersecting two restrictions but
        # inheriting the other side's when only one restricts.  Set intersection
        # of an empty set would grant nothing, which is the wrong reading of
        # "this side did not care".
        classes=_narrower_classes(a.classes, b.classes),
        force_ask=a.force_ask or b.force_ask,
    )


def _narrower_classes(a: Tuple[str, ...], b: Tuple[str, ...]) -> Tuple[str, ...]:
    if not a:
        return b
    if not b:
        return a
    return tuple(sorted(set(a) & set(b)))


def _shallower(a: Optional[int], b: Optional[int]) -> Optional[int]:
    depths = [d for d in (a, b) if d is not None]
    return min(depths) if depths else None


def mask_from_tool_policy(policy: ToolPolicy) -> CapabilityMask:
    """A mode's ``ToolPolicy`` as a capability mask (§8.2).

    Categories become key prefixes; ``edit_guard.file_regex`` becomes the
    ``fs.write``/``fs.edit`` path qualifier; MCP guards become ``mcp.*`` keys.
    This is the mapping that makes F5 a non-issue: the names being matched are
    canonical ids, which are already snake_case.
    """
    if not policy.restrictive and not policy.extra_deny:
        return CapabilityMask.permissive()

    granted: Dict[str, Qualifier] = {}
    edit_paths = _regex_as_globs(policy.edit_guard.file_regex)

    for category in policy.enabled_categories:
        for key in _CATEGORY_TOOLS.get(category, ()):
            capability = key.rstrip(".") + ".*" if key.endswith(".") else key
            if category is ToolCategory.EDIT and edit_paths:
                granted[capability] = Qualifier(paths=edit_paths)
            else:
                granted[capability] = Qualifier()

    return CapabilityMask(
        granted=granted,
        open_by_default=not policy.restrictive,
        denied=tuple(policy.extra_deny),
    )


def _regex_as_globs(file_regex: Optional[str]) -> Tuple[str, ...]:
    """Best-effort translation of a mode's ``fileRegex`` into path globs.

    Only the common anchored-prefix shape (``^src/.+``) is translated. Anything
    else keeps the regex, which :class:`PolicyEngine` still applies through
    ``EditGuard`` — the glob form exists so the *mask* can express the common
    case, not to reimplement regular expressions.
    """
    if not file_regex:
        return ()
    stem = file_regex.lstrip("^")
    for terminator in (".+", ".*", "\\", "(", "[", "|", "$"):
        index = stem.find(terminator)
        if index != -1:
            stem = stem[:index]
    stem = stem.strip()
    if not stem or not stem.rstrip("/"):
        return ()
    return (stem.rstrip("/") + "/**",)


# ---------------------------------------------------------------------------
# Resolved policy
# ---------------------------------------------------------------------------


@dataclass
class ResolvedPolicy:
    """Everything the engine needs, resolved once at run start.

    Resolution — not enforcement — is where F11 is fixed, so it is a single
    named function (:func:`resolve_policy`) rather than three call sites each
    doing their own thing.
    """

    mode: PermissionMode = PermissionMode.NORMAL
    capabilities: CapabilityMask = field(default_factory=CapabilityMask.permissive)
    permissions: PermissionPolicy = field(default_factory=PermissionPolicy)
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy.permissive)
    #: ``False`` denies every mutating tool regardless of mode — a read-only
    #: topology or an explicit `--read-only` run.
    allow_mutation: bool = True
    allow_network: bool = True
    #: Approval timeout, per topology (§8.4).  ``0`` means "never prompt", which
    #: is how a headless run receives denials instead of hanging.
    approval_timeout_s: float = 120.0

    @property
    def read_only(self) -> bool:
        return self.mode is PermissionMode.PLAN or not self.allow_mutation


def restrict_mode(session_mode: Any, requested: Any) -> PermissionMode:
    """Combine the session's mode with a request's, restrictively (§8.1).

    ``auto`` session + ``plan`` request → ``plan``.
    ``normal`` session + ``auto`` request → ``normal``.

    The asymmetry is the whole point: the session record is the authenticated
    channel and a request body is not, so a body can only ever tighten.
    """
    session = _coerce_mode(session_mode, PermissionMode.NORMAL)
    if requested is None or requested == "":
        return session
    asked = _coerce_mode(requested, session)
    return asked if _MODE_RANK[asked] < _MODE_RANK[session] else session


#: Higher = more permitted.  ``plan`` < ``normal`` < ``auto``.
_MODE_RANK = {PermissionMode.PLAN: 0, PermissionMode.NORMAL: 1, PermissionMode.AUTO: 2}


def _coerce_mode(value: Any, default: PermissionMode) -> PermissionMode:
    if isinstance(value, PermissionMode):
        return value
    try:
        return PermissionMode(str(value).strip().lower())
    except (ValueError, AttributeError):
        return default


def resolve_policy(
    *,
    session_mode: Any = None,
    requested_mode: Any = None,
    topology_capabilities: Optional[Mapping[str, Any]] = None,
    session_capabilities: Optional[Mapping[str, Any]] = None,
    tool_policy: Optional[ToolPolicy] = None,
    permissions: Optional[PermissionPolicy] = None,
    read_only: bool = False,
    allow_network: Optional[bool] = None,
    approval_timeout_s: float = 120.0,
) -> ResolvedPolicy:
    """Resolve ``topology ∩ mode ∩ session`` into one policy."""
    mode = restrict_mode(session_mode, requested_mode)

    mask = (
        CapabilityMask.from_mapping(topology_capabilities)
        if topology_capabilities else CapabilityMask.permissive()
    )
    if tool_policy is not None:
        mask = mask.intersect(mask_from_tool_policy(tool_policy))
    if session_capabilities:
        mask = mask.intersect(CapabilityMask.from_mapping(session_capabilities))

    if allow_network is None:
        allow_network = _sandbox_allows_network()

    return ResolvedPolicy(
        mode=mode,
        capabilities=mask,
        permissions=permissions or PermissionPolicy(mode=mode),
        tool_policy=tool_policy or ToolPolicy.permissive(),
        allow_mutation=not read_only,
        allow_network=allow_network,
        approval_timeout_s=approval_timeout_s,
    )


def _sandbox_allows_network() -> bool:
    try:
        from ..settings import get_settings

        return bool(get_settings().sandbox.allow_network)
    except Exception as exc:  # noqa: BLE001 - settings absent in a stripped runtime
        logger.debug("could not read sandbox network setting (%s); assuming off", exc)
        return False


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """Authorize one call.  Never blocks: the loop owns the approval wait.

    ``authorize`` is deliberately synchronous in spirit — it computes a verdict
    and returns.  The ``ask`` verdict is an instruction to the loop, which
    transitions to ``awaiting_approval``, journals the request and resumes
    (§8.4). An engine that awaited a human inside ``authorize`` would make the
    state machine unobservable.
    """

    def __init__(self, policy: Optional[ResolvedPolicy] = None) -> None:
        self.policy = policy or ResolvedPolicy()
        self._manager = PermissionManager(self.policy.permissions)
        #: Capabilities the user approved for the whole session.
        self._session_allowed: set[str] = set()

    # -- session grants ----------------------------------------------------

    def allow_for_session(self, tool: str) -> None:
        """Record an "Allow for session" answer (§8.4's scope)."""
        self._session_allowed.add(tool)

    def allowed_for_session(self, tool: str) -> bool:
        return tool in self._session_allowed

    # -- the pipeline ------------------------------------------------------

    async def authorize(self, call: ToolCall, ctx: Any) -> Decision:
        spec = _spec_for(call, ctx)
        decision = self._seed(spec)

        decision = self._check_capability(call, spec, decision)
        if decision.denied:
            return decision

        decision = self._check_paths(call, spec, decision)
        if decision.denied:
            return decision

        decision = self._classify_command(call, spec, decision)
        if decision.denied:
            return decision

        decision = self._static_rules(call, spec, decision)
        if decision.denied:
            return decision

        return self._verdict(call, spec, decision)

    # -- stages ------------------------------------------------------------

    def _seed(self, spec: Optional[ToolSpec]) -> Decision:
        risk = spec.risk if spec is not None else Risk.APPROVAL
        return Decision(
            verdict=VERDICT_ALLOW,
            risk=risk.value,
            requires_checkpoint=_is_mutating(spec),
        )

    def _check_capability(
        self, call: ToolCall, spec: Optional[ToolSpec], decision: Decision,
    ) -> Decision:
        capability, qualifier = self._granted(call, spec)
        if qualifier is None:
            return decision.escalated_to(
                VERDICT_DENY, f"{capability} is not granted to this session",
            )

        if qualifier.force_ask:
            decision = decision.escalated_to(
                VERDICT_ASK, f"{capability} always requires approval",
            )

        if qualifier.network is False and _touches_network(spec):
            return decision.escalated_to(
                VERDICT_DENY, f"{capability} may not use the network in this session",
            )

        for path in _paths_in(call):
            if not qualifier.permits_path(path):
                return decision.escalated_to(
                    VERDICT_DENY,
                    f"{path} is outside the paths granted for {capability}",
                )

        return decision

    def _granted(
        self, call: ToolCall, spec: Optional[ToolSpec],
    ) -> Tuple[str, Optional[Qualifier]]:
        """The grant governing this call, by tool id or by capability group.

        Matches :func:`gitpilot.toolkit.registry.permits`, and has to: a tool the
        registry offered the model because its *id* was granted must not then be
        refused here because its *group* was not. The id is tried first, so
        ``github.pr.create: ask`` applies its ``ask`` even though the spec's
        capability is the broader ``github.pr.write``.
        """
        if spec is None:
            return call.tool, self.policy.capabilities.qualifier(call.tool)

        if spec.id != spec.capability:
            by_id = self.policy.capabilities.qualifier(spec.id)
            if by_id is not None:
                return spec.id, by_id

        return spec.capability, self.policy.capabilities.qualifier(spec.capability)

    def _check_paths(
        self, call: ToolCall, spec: Optional[ToolSpec], decision: Decision,
    ) -> Decision:
        """``blocked_paths`` and the mode's edit guard.

        Applied to reads as well as writes, which is the point of routing every
        call through here: a 1.5B model's synthesized ``fs.read`` of ``.env`` is
        refused exactly as a frontier model's would be.
        """
        for path in _paths_in(call):
            try:
                self._manager._check_path(path)   # noqa: SLF001 - its only caller
            except PermissionError as exc:
                return decision.escalated_to(VERDICT_DENY, str(exc))

            if _writes(spec) and not self.policy.tool_policy.edit_guard.matches(path):
                return decision.escalated_to(
                    VERDICT_DENY, f"{path} is excluded by this mode's edit guard",
                )
        return decision

    def _classify_command(
        self, call: ToolCall, spec: Optional[ToolSpec], decision: Decision,
    ) -> Decision:
        """Semantic classification for anything that runs a shell (§8.3).

        Escalate-only, enforced by :meth:`Decision.escalated_to`: classification
        may raise the verdict but never lower it below the tool's declared risk.
        """
        command = _command_in(call)
        if not command:
            static = _STATIC_COMMAND_CLASSES.get(spec.id if spec is not None else call.tool)
            return replace(decision, command_class=static) if static else decision

        from .command_class import classify_command

        verdict = classify_command(command, allow_network=self.policy.allow_network)

        # A capability may name the classes it accepts.  This is a *denial*, not
        # an escalation: a read-only inspection run being asked to approve `npm
        # install` is a prompt nobody should have to answer.
        capability = spec.capability if spec is not None else call.tool
        qualifier = self.policy.capabilities.qualifier(capability)
        if qualifier is not None and not qualifier.permits_class(verdict.command_class):
            return decision.escalated_to(
                VERDICT_DENY,
                f"{capability} only permits {', '.join(qualifier.classes)} commands; "
                f"this one {verdict.description}",
                risk=verdict.risk,
                command_class=verdict.command_class,
            )

        return decision.escalated_to(
            verdict.verdict,
            verdict.reason,
            risk=verdict.risk,
            command_class=verdict.command_class,
        )

    def _read_only_permits(
        self, call: ToolCall, spec: Optional[ToolSpec], decision: Decision,
    ) -> bool:
        """Whether a read-only run may make this call — Batch V4-G3.

        A tool with no mutating effect is fine, as it always was. What is new is
        that an *executing* tool is judged on the command class the classifier
        computed rather than on the tool's declared effects: ``terminal.run``
        declares ``WRITES_FS`` because it can write, not because ``cat Makefile``
        does.

        ``READ_ONLY`` passes on the classifier's own evidence. ``TEST`` passes only
        when the capability names it — and only when the class is one this module
        already considers safe for a read-only run, so a document writing
        ``classes: [DESTRUCTIVE]`` gains nothing.
        """
        if not _is_mutating(spec):
            return True

        command_class = decision.command_class
        if not command_class:
            return False
        if command_class in READ_ONLY_COMMAND_CLASSES:
            return True
        if command_class not in OPT_IN_READ_ONLY_CLASSES:
            return False

        capability = spec.capability if spec is not None else call.tool
        qualifier = self.policy.capabilities.qualifier(capability)
        return qualifier is not None and command_class in qualifier.classes

    def _static_rules(
        self, call: ToolCall, spec: Optional[ToolSpec], decision: Decision,
    ) -> Decision:
        if self.policy.read_only and not self._read_only_permits(call, spec, decision):
            why = (
                "plan mode is read-only"
                if self.policy.mode is PermissionMode.PLAN
                else "this run is read-only"
            )
            return decision.escalated_to(
                VERDICT_DENY, f"{call.tool} would modify state and {why}",
            )

        action = _TOOL_ACTIONS.get(spec.id if spec is not None else call.tool)
        if action is not None and self.policy.permissions.allowed_actions is not None:
            if action not in self.policy.permissions.allowed_actions:
                return decision.escalated_to(
                    VERDICT_DENY, f"{action.value} is not in this session's allowed actions",
                )
        return decision

    def _verdict(
        self, call: ToolCall, spec: Optional[ToolSpec], decision: Decision,
    ) -> Decision:
        if decision.verdict == VERDICT_DENY:
            return decision

        if self.policy.mode is PermissionMode.AUTO:
            # Auto still journals, and still checkpoints — nobody saw this
            # coming, so the snapshot matters more here, not less.
            return replace(
                decision, verdict=VERDICT_ALLOW,
                reason=decision.reason or "auto mode",
            )

        if self.allowed_for_session(call.tool):
            return replace(
                decision, verdict=VERDICT_ALLOW,
                reason=f"{call.tool} allowed for this session",
            )

        if decision.verdict == VERDICT_ASK:
            return decision

        action = _TOOL_ACTIONS.get(spec.id if spec is not None else call.tool)
        risk = Risk(decision.risk)

        if risk is Risk.SAFE:
            # A safe tool asks only if the user's policy says this *action*
            # should. `require_confirmation` is an escalation channel — it can
            # add an approval, never remove one, so a tool declared APPROVAL
            # cannot be waved through by an action list that omits it. Reading
            # it the other way round is what made `fs.write` silently allowed:
            # the inherited default lists DELETE_FILE, GIT_PUSH, MERGE_PR and
            # RUN_COMMAND, and not WRITE_FILE.
            if action is not None and self._manager.needs_confirmation(action):
                return decision.escalated_to(
                    VERDICT_ASK, f"{action.value} requires confirmation in this session",
                )
            return replace(decision, verdict=VERDICT_ALLOW, reason=decision.reason or "read-only")

        return decision.escalated_to(
            VERDICT_ASK, decision.reason or f"{call.tool} needs approval ({risk.value})",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_for(call: ToolCall, ctx: Any) -> Optional[ToolSpec]:
    registry = getattr(getattr(ctx, "tool_context", None), "extras", {}) or {}
    registry = registry.get("registry") if isinstance(registry, Mapping) else None
    if registry is None:
        return None
    try:
        spec = registry.spec(call.tool)
    except Exception:  # noqa: BLE001 - an unknown tool is the registry's to report
        return None
    return spec if isinstance(spec, ToolSpec) else None


def _is_mutating(spec: Optional[ToolSpec]) -> bool:
    if spec is None:
        return True   # unknown ⇒ treat as mutating; the safe direction
    return bool(spec.effects & MUTATING_EFFECTS) or Effect.EXECUTES in spec.effects


def _writes(spec: Optional[ToolSpec]) -> bool:
    return spec is not None and Effect.WRITES_FS in spec.effects


def _touches_network(spec: Optional[ToolSpec]) -> bool:
    if spec is None:
        return False
    return bool(spec.effects & {Effect.NETWORK, Effect.GIT_REMOTE, Effect.FORGE_WRITE})


def _paths_in(call: ToolCall) -> Sequence[str]:
    found = []
    for key in _PATH_ARGS:
        value = call.arguments.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    return found


def _command_in(call: ToolCall) -> str:
    for key in ("command", "cmd", "code"):
        value = call.arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""
