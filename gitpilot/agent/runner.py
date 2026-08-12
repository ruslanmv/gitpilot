# gitpilot/agent/runner.py
"""Starting, streaming and cancelling a run — Batch V4-C3.

:class:`AgentRunner` is what an endpoint calls.  It assembles the pieces —
profile, provider, registry, adapter, context, journal — bridges the loop's
events onto the session's :class:`~gitpilot.agent_events.AgentEventBus`, and
keeps a handle so the run can be cancelled.

Engine selection lives in :func:`should_use_loop` and is deliberately narrow:
the ``agent_loop`` flag *and* a topology that asked for it.  T9
(``tool_augmented_react``) is the pilot because it is the project's own sketch of
this engine — a flow graph naming a tool-def pruner, a sandbox runner and an
approval batcher around a Thought/Action/Observation agent, with nothing behind
it.  Every other topology keeps today's path byte for byte.

One thing changes for everyone the moment this is on: **folder-only and
local-git sessions can stream**.  Today ``StreamingAgentExecutor`` closes the v2
stream immediately for any session without a GitHub repo, so clients fall back to
batch chat — which is precisely the local-Ollama case this programme is for.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .. import agent_events as evt
from ..toolkit.builtins import build_default_registry
from ..toolkit.registry import (  # noqa: F401 — RepoBinding re-exported for the API layer
    ALL_CAPABILITIES,
    LocalWorkspace,
    RepoBinding,
    ToolExecutionContext,
    ToolRegistry,
)
from .adapter import build_adapter
from .context import AgentContext, AgentResult, BudgetState
from .journal import RunJournal, SeedContext
from .loop import (
    FLAG_AGENT_LOOP,
    AgentLoop,
    LoopEvents,
    PermissivePolicy,
    Verification,
    new_run_id,
)
from .model_profile import ModelProfile, profile_for_settings
from .policy import FLAG_AGENT_POLICY
from .prompts import build_system_prompt, task_message

logger = logging.getLogger(__name__)

#: The topology piloting the engine in Phase C.
PILOT_TOPOLOGY_ID = "tool_augmented_react"

#: Routes every topology whose v2 document declares ``engine: agentic_loop`` to
#: this engine (Batch V4-G2; the flip itself is V4-G5, §15.4).
FLAG_AGENT_LOOP_DEFAULT = "agent_loop_default"

#: **This line is the flip** (Batch V4-G5, §15.4).
#:
#: ``default`` carries the agentic policy document as of V4-G5, but a document
#: declares policy and this decides who executes it. Setting this to ``True``
#: makes the agentic engine serve every migrated topology for users who have not
#: set the flag either way — which is every user.
#:
#: It stays ``False`` until the §18 benchmark gate is demonstrated: success rate at
#: least matching legacy on all three model tiers, with wall time and tokens inside
#: a 1.5× budget. Run ``python -m bench --out results.json`` and then
#: ``python -m bench.gate results.json``; a green gate is the evidence for changing
#: this, and it is one line so the change is reviewable on its own.
#:
#: Until then, a user can opt in per-machine (``.gitpilot/flags.json``,
#: ``GITPILOT_FLAGS=agent_loop_default``) and back out with the ``classic``
#: topology, which keeps the CrewAI routing for one release cycle.
AGENT_LOOP_DEFAULT_ON = False

#: The topology that carries the default agentic policy — ``default`` itself as
#: of Batch V4-G5.
DEFAULT_AGENTIC_TOPOLOGY_ID = "default"

#: The pre-v4 CrewAI routing, kept under its own name for one release cycle so a
#: user who wants it has something to ask for (§15.4).
CLASSIC_TOPOLOGY_ID = "classic"

#: Registers the workspace's MCP servers as ``mcp.<server>.<tool>`` registry
#: entries (Batch V4-H1).  Off by default: an MCP server is a third party, and a
#: run that starts calling one because a config file exists is not a run the user
#: asked for.
FLAG_MCP_TOOLS = "mcp_tools"

#: The CrewAI ``sequential_pipeline``/``single_task`` engines — **on** by default
#: (Batch V4-G3).  Turning it off is how a user says "never run the legacy
#: engines": a topology with an agentic document then runs on the loop whatever
#: ``agent_loop_default`` says, and there is no silent third behaviour where a
#: topology has opted out of CrewAI and has nothing to run.
FLAG_LEGACY_CREW_ENGINES = "legacy_crew_engines"

#: Loop event type → the ``agent_events`` factory that renders it.  Events with
#: no factory yet are emitted as ``status_change`` carrying their name, so a
#: client sees them rather than nothing while Batch V4-E5 adds first-class
#: rendering.
_EVENT_FACTORIES = {
    "text_delta": lambda p: evt.text_delta(p.get("text", "")),
    "agent_message": lambda p: evt.text_delta(p.get("text", "")),
    "tool_start": lambda p: evt.tool_start(
        p.get("id", ""), p.get("name", ""), p.get("args") or {},
    ),
    "tool_result": lambda p: evt.tool_result(
        p.get("id", ""), p.get("name", ""),
        "denied" if p.get("denied") else ("ok" if p.get("ok") else str(p.get("error") or "error")),
        is_error=not p.get("ok", False),
    ),
    "file_write": lambda p: evt.file_write(p.get("path", ""), p.get("action", "modify")),
    "test_result": lambda p: evt.test_result(
        framework=str(p.get("framework") or "tests"),
        passed=int(p.get("passed") or 0),
        failed=int(p.get("failed") or 0),
        skipped=int(p.get("skipped") or 0),
        exit_code=int(p.get("exit_code") or 0),
    ),
    "error": lambda p: evt.agent_error(str(p.get("error") or ""), bool(p.get("recoverable", True))),
    # The event that closes an SSE stream.  A consumer reads until it sees `done`
    # or `error`, so this mapping is what stops a finished run from leaving the
    # client on keepalives until it times out.
    "run_finished": lambda p: evt.agent_done(
        usage=p.get("usage") or {},
        summary=str(p.get("answer") or "")[:500],
    ),
}


def apply_topology_policy(
    request: "RunRequest", *, workspace: Optional[Path] = None,
) -> "RunRequest":
    """Fill *request*'s policy fields from its topology's v2 document — Batch V4-G1.

    Returns the request **unchanged** when the topology has no document. That is
    the additive guarantee for Phase G: the nine v1 entries keep the unrestricted
    Phase C behaviour until each one's document lands (§15.3), so migrating a
    topology cannot change one that has not been migrated.

    Every field it fills is filled *restrictively*. A caller that already asked
    for read-only stays read-only even against a permissive document; a caller
    with a tighter iteration cap keeps it; the permission mode becomes the
    stricter of the caller's and the document's, and the session's own mode is
    combined afterwards by :func:`~gitpilot.agent.policy.resolve_policy` — one
    place doing session ∩ request, as §8.1 requires.
    """
    from dataclasses import replace

    try:
        from ..topology.registry import get_policy
    except Exception:  # noqa: BLE001 - a stripped runtime has no topology package
        logger.debug("topology registry unavailable; running unrestricted", exc_info=True)
        return request

    policy = get_policy(request.topology_id, workspace=workspace)
    if policy is None:
        return request

    return replace(
        request,
        topology_capabilities=(
            request.topology_capabilities
            if request.topology_capabilities is not None
            else dict(policy.capabilities) or None
        ),
        read_only=request.read_only or policy.read_only,
        requested_mode=_stricter_mode(
            request.requested_mode, policy.approval.requested_mode(),
        ),
        approvals=request.approvals or ("none" if policy.approval.headless else None),
        verification=policy.verification.tests.value,
        max_fix_cycles=policy.verification.max_fix_cycles,
        max_iterations=_tighter(request.max_iterations, policy.limits.max_iterations),
        max_tool_calls=_tighter(request.max_tool_calls, policy.limits.max_tool_calls),
        max_runtime_seconds=_tighter(
            request.max_runtime_seconds, policy.limits.max_runtime_seconds,
        ),
        dialect=request.dialect or (
            None if policy.execution.dialect.value == "auto"
            else policy.execution.dialect.value
        ),
        role=request.role or policy.agent.role,
    )


def _tighter(requested: Optional[int], from_policy: int) -> int:
    return min(requested, from_policy) if requested else from_policy


def _stricter_mode(requested: Optional[str], from_policy: Optional[str]) -> Optional[str]:
    """The more restrictive of two requested modes, or whichever is present."""
    if not requested:
        return from_policy
    if not from_policy:
        return requested
    from .policy import restrict_mode

    return restrict_mode(requested, from_policy).value


def should_use_loop(
    topology_id: str,
    *,
    flag_enabled: Optional[bool] = None,
    default_flag_enabled: Optional[bool] = None,
    legacy_engines_enabled: Optional[bool] = None,
) -> bool:
    """Whether this request goes to the agentic engine.

    Three gates, because there are three ways to get here (§15.4):

    * **The pilot.** ``agent_loop`` + T9. Narrow on purpose: turning that flag on
      cannot change behaviour for a user who did not pick T9.
    * **A migrated topology.** ``agent_loop_default`` + a topology whose v2
      document declares ``engine: agentic_loop`` (Batch V4-G2 onward). Off by
      default until the benchmark gate passes, so authoring a document does not
      by itself reroute anybody — the document describes the policy, this decides
      who runs it.
    * **Opting out of CrewAI.** ``legacy_crew_engines`` off + an agentic document
      (Batch V4-G3). Without this, "no legacy engines" would leave a migrated
      pipeline with nothing to run at all.

    Every other combination keeps today's path byte for byte.
    """
    topology_id = topology_id or ""

    if flag_enabled is None:
        from ..flags import is_on

        flag_enabled = is_on(FLAG_AGENT_LOOP, default=False)
    if bool(flag_enabled) and topology_id == PILOT_TOPOLOGY_ID:
        return True

    if not topology_id:
        return False

    from ..flags import is_on

    if default_flag_enabled is None:
        default_flag_enabled = is_on(FLAG_AGENT_LOOP_DEFAULT, default=AGENT_LOOP_DEFAULT_ON)
    if legacy_engines_enabled is None:
        legacy_engines_enabled = is_on(FLAG_LEGACY_CREW_ENGINES, default=True)

    if not default_flag_enabled and legacy_engines_enabled:
        return False
    return _declares_agentic_loop(topology_id)


def _declares_agentic_loop(topology_id: str) -> bool:
    """Whether *topology_id*'s document asks for the agentic engine."""
    try:
        from ..topology.registry import get_policy
    except Exception:  # noqa: BLE001 - a stripped runtime has no topology package
        return False
    policy = get_policy(topology_id)
    return bool(policy and policy.execution.engine.value == "agentic_loop")


@dataclass
class RunRequest:
    """Everything needed to start a run."""

    task: str
    session_id: str = "ephemeral"
    topology_id: str = PILOT_TOPOLOGY_ID
    workspace_root: Optional[Path] = None
    repo: Optional[RepoBinding] = None
    transcript_index: int = 0
    prior_messages: Optional[List[Dict[str, Any]]] = None
    read_only: bool = False
    max_iterations: Optional[int] = None
    model: Optional[str] = None
    journal_root: Optional[Path] = None
    #: The session's persisted permission mode — the single source of truth
    #: (§8.1, F11).  ``None`` means "normal".
    session_mode: Optional[str] = None
    #: A per-request mode.  It may only *restrict* the session's; a chat body is
    #: not an authenticated escalation channel.
    requested_mode: Optional[str] = None
    #: Capabilities the topology declares (Batch V4-G1 fills these from the
    #: topology document); absent means unrestricted.
    topology_capabilities: Optional[Dict[str, Any]] = None
    #: The active mode's tool policy, from ``modes.yaml``.
    tool_policy: Optional[Any] = None
    #: Who answers approvals.  ``None`` asks through the process registry;
    #: ``"none"`` denies without prompting, which is what a headless run wants.
    approvals: Optional[Any] = None
    #: What snapshots before a mutating call.  ``None`` builds one when the
    #: session has a workspace.
    session_manager: Optional[Any] = None
    #: How many delegations deep this run already is.  A child is built with
    #: ``depth + 1``, and `agent.delegate` is withheld once the qualifier's
    #: ``max_depth`` is reached (Batch V4-E2).
    depth: int = 0
    #: Set on a child so its events can be nested under the call that spawned it.
    parent_call_id: str = ""
    #: Whether this run must prove its changes work (Batch V4-E3): ``off``,
    #: ``auto`` (when the project has tests) or ``required``.  Batch V4-G1 sets it
    #: from the topology document.
    verification: str = "auto"
    max_fix_cycles: int = 2
    #: Per-run limits.  ``None`` keeps :class:`BudgetState`'s §5.5 defaults; a
    #: topology document tightens them (Batch V4-G1).
    max_tool_calls: Optional[int] = None
    max_runtime_seconds: Optional[int] = None
    #: A dialect pinned by the topology (``execution.dialect``).  ``None`` lets the
    #: ``ModelProfile`` decide, which is the right answer for everything except a
    #: topology that exists to force LITE (T8).
    dialect: Optional[str] = None
    #: The active ``modes.yaml`` slug, if the session has one (Batch V4-H2).  A
    #: mode contributes a persona to the system prefix, narrows the capability mask
    #: and may declare its own MCP servers.
    mode_slug: str = ""
    #: The topology's ``agent.role``, which selects a prompt profile
    #: (:data:`gitpilot.agent.prompts.ROLE_PROMPTS`, Batch V4-G3).  Empty means the
    #: generic engineer instructions.
    role: str = ""


class AgentRunner:
    """Builds and drives one loop, and knows how to stop it."""

    def __init__(self) -> None:
        self._loops: Dict[str, AgentLoop] = {}
        self._tasks: Dict[str, "asyncio.Task[Any]"] = {}

    # -- assembly ---------------------------------------------------------

    def build(
        self,
        request: RunRequest,
        *,
        bus: Optional[Any] = None,
        registry: Optional[ToolRegistry] = None,
        provider: Optional[Any] = None,
        profile: Optional[ModelProfile] = None,
    ) -> tuple[AgentLoop, AgentContext]:
        registry = registry or build_default_registry(
            # A session with no checkout cannot use git tools; offering them
            # would spend a turn discovering that.
            include_git=request.workspace_root is not None,
            include_forge=request.repo is not None,
        )
        profile = profile or profile_for_settings(
            model=request.model, workspace=request.workspace_root,
        )
        if request.dialect:
            from .contracts import Dialect

            try:
                profile = profile.forced(Dialect(request.dialect))
            except ValueError:
                logger.warning("ignoring unknown pinned dialect %r", request.dialect)
        if provider is None:
            from .providers import build_provider

            provider = build_provider(model=request.model)

        adapter = build_adapter(provider, profile, registry)

        run_id = new_run_id()
        journal = RunJournal(
            run_id=run_id, session_id=request.session_id, root=request.journal_root,
        )

        tool_context = ToolExecutionContext(
            workspace=LocalWorkspace(root=request.workspace_root) if request.workspace_root else None,
            repo=request.repo,
            session_id=request.session_id,
            run_id=run_id,
            extras={"registry": registry},
        )

        budgets = BudgetState()
        if request.max_iterations:
            budgets.max_iterations = int(request.max_iterations)
        if request.max_tool_calls:
            budgets.max_tool_calls = int(request.max_tool_calls)
        if request.max_runtime_seconds:
            budgets.max_runtime_seconds = int(request.max_runtime_seconds)

        policy, capabilities = self._authorization(request)

        # Delegation, when there is depth left to use.  Registering the tool is
        # what makes it visible, so a run at its depth limit simply does not have
        # it — better than offering a tool whose every call is refused.
        if _can_delegate(policy, request.depth):
            from . import delegation

            delegation.register(registry)
            tool_context.extras["delegate_spawn"] = self._spawner(
                request, policy, capabilities, provider, profile, bus, journal,
            )

        base_system = build_system_prompt(
            profile,
            read_only=request.read_only or _is_read_only(policy),
            # Only mentioned when the model can actually see the tool, so a
            # run never spends a turn calling something it was told about and
            # was not given.
            require_verification=request.verification == "required",
            with_todo=_offers_todo(adapter, capabilities),
            role=request.role,
        )

        ctx = AgentContext(
            run_id=run_id,
            task=request.task,
            journal=journal,
            model_profile=profile,
            tool_context=tool_context,
            session_id=request.session_id,
            topology_id=request.topology_id,
            capabilities=capabilities,
            system_payload=self._system_text(
                request, base_system, profile, registry, capabilities,
            ),
            messages=[*(request.prior_messages or []), task_message(request.task)],
            budgets=budgets,
        )

        # A LITE run needs the real file list up front: every path the model
        # names is validated against it, which is what stops a hallucinated path
        # from being applied.
        prepare = getattr(adapter, "prepare", None)
        if callable(prepare):
            prepare(request.task, _known_files(request))

        loop = AgentLoop(
            adapter, registry,
            verification=Verification(
                tests=request.verification, max_fix_cycles=request.max_fix_cycles,
            ),
            policy=policy,
            approvals=self._approver(request, policy),
            hooks=self._hooks(request),
            checkpointer=self._checkpointer(request),
            events=LoopEvents(_bus_bridge(bus)) if bus else LoopEvents(),
        )
        self._loops[request.session_id] = loop
        return loop, ctx

    def _system_text(
        self,
        request: RunRequest,
        base_system: str,
        profile: ModelProfile,
        registry: ToolRegistry,
        capabilities: Any,
    ) -> str:
        """The run's system prefix — Batch V4-H2.

        With ``agent_system_payload`` off this is exactly the string Phase C
        produced, byte for byte. With it on, ``prompt_cache.build_system_blocks``
        composes the project's real prefix around it: the mode's persona,
        ``AGENTS.md``, rules, and a digest of the tool definitions the *mask*
        permits — so a capability change busts the cache rather than serving a
        prefix that describes tools this run cannot use.
        """
        from .system_payload import build_system_text, payload_enabled

        if not payload_enabled():
            return base_system

        try:
            return build_system_text(
                base_system=base_system,
                workspace=request.workspace_root,
                mode=self._mode(request),
                # Rendered under this run's capabilities, not the whole registry:
                # the digest has to change when the mask does.
                tool_defs=registry.render(capabilities).schemas,
                provider=profile.provider,
            )
        except Exception:  # noqa: BLE001 - a prompt that will not compose is not a run-ender
            logger.warning("could not compose the system payload; using the base prompt",
                           exc_info=True)
            return base_system

    def _mode(self, request: RunRequest) -> Any:
        """The session's active mode, resolved once per run."""
        if not request.mode_slug:
            return None
        from .system_payload import resolve_mode

        return resolve_mode(request.mode_slug, request.workspace_root)

    # -- the safety stack -------------------------------------------------

    def _authorization(self, request: RunRequest) -> tuple[Any, Any]:
        """The policy and the capability mask for this run.

        With ``agent_policy`` off this returns Phase C's permissive stub and the
        permissive mask, so the flag's off-branch is byte-identical to what
        shipped rather than to something new.
        """
        if not _policy_enabled():
            return PermissivePolicy(), ALL_CAPABILITIES

        from .policy import PolicyEngine, resolve_policy

        resolved = resolve_policy(
            session_mode=request.session_mode,
            requested_mode=request.requested_mode,
            topology_capabilities=request.topology_capabilities,
            # A mode's own ``ToolPolicy`` when the caller did not pass one, so
            # `modes.yaml` narrows what a topology granted (Batch V4-H2).
            tool_policy=request.tool_policy or _mode_tool_policy(request),
            read_only=request.read_only,
        )
        return PolicyEngine(resolved), resolved.capabilities

    def _approver(self, request: RunRequest, policy: Any) -> Optional[Any]:
        if request.approvals is not None and request.approvals != "none":
            return request.approvals

        from .loop import DenyingApprovals

        if request.approvals == "none":
            return DenyingApprovals()
        if not _policy_enabled():
            return None

        from .approvals import LoopApprovals

        timeout = getattr(getattr(policy, "policy", None), "approval_timeout_s", 120.0)
        return LoopApprovals(
            session_id=request.session_id,
            timeout_s=timeout,
            on_session_scope=getattr(policy, "allow_for_session", None),
        )

    def _hooks(self, request: RunRequest) -> Optional[Any]:
        """A project's ``.gitpilot/hooks.json``, if it has one (Batch V4-D5)."""
        if not _policy_enabled() or request.workspace_root is None:
            return None

        from .hook_bridge import LoopHooks

        return LoopHooks.for_workspace(
            request.workspace_root, session_id=request.session_id,
        )

    def _spawner(
        self,
        request: RunRequest,
        policy: Any,
        capabilities: Any,
        provider: Any,
        profile: ModelProfile,
        bus: Optional[Any],
        journal: RunJournal,
    ) -> Any:
        """The callback ``agent.delegate`` invokes — Batch V4-E2.

        Everything that makes a delegation safe is enforced here rather than in
        the tool, because the tool runs on the model's arguments and this runs on
        the parent's actual state.
        """
        from . import delegation
        from .delegation import DelegationError

        counter = {"n": 0}

        async def spawn(
            *,
            call: Any,
            template: delegation.SubagentTemplate,
            task: str,
            expected: str = "",
            max_iterations: Optional[int] = None,
        ) -> Any:
            if delegation.remaining_depth(policy, request.depth) <= 0:
                raise DelegationError(
                    "This run has reached its delegation depth limit. Do the work "
                    "yourself rather than handing it on."
                )

            child_mask = delegation.child_capabilities(capabilities, template)
            if not any(child_mask.allows(key) for key in template.capabilities):
                raise DelegationError(
                    f"A {template.name} subagent needs capabilities this run does "
                    "not have, so it could not do anything useful."
                )

            counter["n"] += 1
            index = counter["n"]

            child_request = RunRequest(
                task=template.brief(task, expected),
                session_id=request.session_id,
                topology_id=f"sub:{template.name}",
                workspace_root=request.workspace_root,
                repo=request.repo,
                read_only=request.read_only or template.read_only,
                max_iterations=int(max_iterations or template.max_iterations),
                model=request.model,
                journal_root=request.journal_root,
                session_mode=request.session_mode,
                requested_mode=request.requested_mode,
                # The ceiling the child is built with. `_authorization` intersects
                # it again with the mode, so a plan-mode parent yields a
                # plan-mode child.
                topology_capabilities=dict(template.capabilities),
                approvals="none",   # a subagent has no user to ask
                verification="off",  # a read-only subagent has nothing to verify
                depth=request.depth + 1,
                parent_call_id=getattr(call, "id", ""),
            )

            loop, ctx = AgentRunner().build(
                child_request,
                # The child's events go through the parent's *tagged* bridge, so a
                # UI can nest them; handing it the raw bus would make a subagent's
                # reads look like the parent's own.
                bus=None,
                provider=provider,
                # The parent's profile, not a fresh resolution from settings: a
                # child that re-resolved could end up on a different dialect from
                # its parent, so a frontier-model run would spawn a LITE subagent
                # and get a phase machine where it expected tool calls.
                profile=profile,
            )
            # Intersected against the parent's *live* mask, not just the mode:
            # `_authorization` only knows what the request told it, and the whole
            # safety argument is that a child cannot exceed its parent.
            ctx.capabilities = child_mask
            # And a child is *always* policy-enforced, regardless of the
            # `agent_policy` flag. The flag exists to make Phase D revertible for
            # the paths that predate it; a subagent has no such history, and a
            # capability mask that were merely advisory would make `agent.delegate`
            # exactly the escalation channel the intersection above prevents.
            loop.policy = _child_policy(child_mask, child_request)
            ctx.journal = RunJournal(
                run_id=f"sub-{index}",
                session_id=request.session_id,
                directory=delegation.child_journal_path(journal, index).parent,
            )
            ctx.system_payload = build_system_prompt(
                ctx.model_profile,
                read_only=child_request.read_only,
                role=template.role,
            )
            if bus is not None:
                loop.events = LoopEvents(_nested_bridge(bus, call, template.name))

            await self._announce(bus, call, template, index)
            result = await loop.run(ctx)
            await self._announce_done(bus, call, template, result)

            return _structured_return(result)

        return spawn

    async def _announce(
        self, bus: Optional[Any], call: Any, template: Any, index: int,
    ) -> None:
        if bus is None:
            return
        await _nested_bridge(bus, call, template.name)(
            "agent_delegated",
            {"agent": template.name, "title": template.title, "index": index},
        )

    async def _announce_done(
        self, bus: Optional[Any], call: Any, template: Any, result: Any,
    ) -> None:
        if bus is None:
            return
        await _nested_bridge(bus, call, template.name)(
            "agent_delegate_done",
            {"agent": template.name, "status": getattr(result, "status", "completed")},
        )

    def _checkpointer(self, request: RunRequest) -> Optional[Any]:
        """A snapshot before every mutating call (Batch V4-D4)."""
        if request.session_manager is None or request.workspace_root is None:
            return None

        from .checkpointing import SessionCheckpointer

        return SessionCheckpointer(
            session_id=request.session_id,
            session_manager=request.session_manager,
            workspace=request.workspace_root,
        )

    # -- running ----------------------------------------------------------

    async def run(
        self,
        request: RunRequest,
        *,
        bus: Optional[Any] = None,
        **build_kwargs: Any,
    ) -> AgentResult:
        # MCP registration is async and ``build`` is not, so the registry is
        # assembled here when the caller did not supply one. A caller that passes a
        # registry gets exactly the registry it passed — tests and subagent spawns
        # build one deliberately.
        if build_kwargs.get("registry") is None:
            registry = await self._registry_for(request)
            if registry is not None:
                build_kwargs["registry"] = registry

        # The topology's document is applied here rather than in ``build`` so the
        # low-level assembly stays exactly what its caller asked for: tests and
        # subagent spawns build a request deliberately, and having the registry
        # rewrite it underneath them would make the request they wrote a lie.
        request = apply_topology_policy(request, workspace=request.workspace_root)
        loop, ctx = self.build(request, bus=bus, **build_kwargs)
        seed = SeedContext(
            task=request.task,
            session_id=request.session_id,
            transcript_index=request.transcript_index,
            messages=list(request.prior_messages or []),
            topology_id=request.topology_id,
            model=ctx.model_profile.model,
            dialect=ctx.model_profile.dialect.value,
        )
        try:
            return await loop.run(ctx, seed)
        finally:
            self._loops.pop(request.session_id, None)

    async def _registry_for(self, request: RunRequest) -> Optional[ToolRegistry]:
        """The default registry, plus this workspace's MCP tools when enabled.

        Returns ``None`` on any failure so ``build`` falls back to constructing the
        registry itself: an unreachable MCP server, or a missing admin store, must
        not stop a run that was never about MCP.
        """
        registry = build_default_registry(
            include_git=request.workspace_root is not None,
            include_forge=request.repo is not None,
        )

        from ..flags import is_on

        if not is_on(FLAG_MCP_TOOLS, default=False):
            return registry

        try:
            from ..toolkit.mcp import register_from_store

            report = await register_from_store(
                registry, workspace=request.workspace_root,
            )
        except Exception:  # noqa: BLE001 - MCP is additive; its failure is not the run's
            logger.warning("could not register MCP tools", exc_info=True)
            return registry

        if report.registered:
            logger.info(
                "registered %d MCP tools from %s",
                len(report.registered), ", ".join(report.servers),
            )
        for server, reason in report.unreachable.items():
            logger.warning("MCP server %s is unreachable: %s", server, reason)
        return registry

    # -- resuming ---------------------------------------------------------

    async def resume(
        self,
        journal_path: Any,
        *,
        bus: Optional[Any] = None,
        session_id: str = "",
        workspace_root: Optional[Path] = None,
        repo: Optional[RepoBinding] = None,
        session_manager: Optional[Any] = None,
        **build_kwargs: Any,
    ) -> AgentResult:
        """Pick a run up where its journal stops — Batch V4-F1.

        The journal is the whole input. Everything the run knew that a fresh
        context would not — the ledger, the file sets, the TODO list, how much
        budget it had spent, and whether it was waiting on a human — comes out of
        it, and the transcript is *re-rendered* for whichever dialect is resuming
        rather than restored verbatim. That is what makes a resume survive the
        operator swapping the model between the crash and the recovery.
        """
        from .resume import ResumeError, replay, restore

        path = Path(journal_path)
        run = replay(path)

        if run.seed is None:
            raise ResumeError(
                f"{path.name} has no start record; there is nothing to resume."
            )
        if run.status is not None:
            # Not an error the user caused, and not something to paper over by
            # starting a second run under the same id.
            raise ResumeError(
                f"that run already finished ({run.status}); start a new one instead."
            )

        request = RunRequest(
            task=run.seed.task,
            session_id=session_id or run.seed.session_id or "ephemeral",
            topology_id=run.seed.topology_id or PILOT_TOPOLOGY_ID,
            workspace_root=workspace_root,
            repo=repo,
            transcript_index=run.seed.transcript_index,
            session_manager=session_manager,
        )

        loop, ctx = self.build(request, bus=bus, **build_kwargs)

        # The journal continues rather than a second file starting: one run, one
        # record, or the safety invariants would have to be asserted over a merge.
        ctx.run_id = run.run_id
        ctx.journal = RunJournal(
            run_id=run.run_id, session_id=request.session_id,
            directory=path.parent,
        ).reopen()
        ctx.tool_context.run_id = run.run_id

        restore(run, ctx, loop.adapter)

        dialect = ctx.model_profile.dialect.value
        ctx.journal.state_change(
            "running",
            resumed_from_seq=run.last_seq,
            recorded_dialect=run.recorded_dialect,
            dialect=dialect,
        )
        await loop.events.emit(
            "run_resumed",
            run_id=run.run_id, from_seq=run.last_seq,
            iteration=run.iteration, dialect=dialect,
            redialected=bool(run.recorded_dialect and run.recorded_dialect != dialect),
        )

        if run.pending_approval is not None:
            # Re-ask. The alternatives are executing a call the user never
            # approved, or waiting on a future nobody holds — Batch V4-D3 chose
            # "a restart denies" as the safe stopgap, and this is the promise it
            # was standing in for.
            await self._rearm_approval(loop, ctx, run.pending_approval)

        self._loops[request.session_id] = loop
        try:
            # No seed: `run.begin` would write a second `run_started` line.
            return await loop.run(ctx, seed=None, resumed=True)
        finally:
            self._loops.pop(request.session_id, None)

    async def _rearm_approval(self, loop: AgentLoop, ctx: AgentContext, call: Any) -> None:
        """Put the model back in front of the decision it was waiting on.

        Fed back as an observation rather than re-issued as a call: the model
        chose that call in a context this process no longer has, and the honest
        thing is to tell it what happened and let it choose again.
        """
        from ..toolkit.registry import ToolCall, ToolResult

        pending = ToolCall(
            id=call.call_id, tool=call.tool, arguments=call.arguments,
        )
        result = ToolResult.failure(
            pending,
            f"This run was interrupted while waiting for approval of {call.tool}. "
            "The request was not carried out. Ask again if you still need it.",
            error="approval_interrupted",
        )
        ctx.record(pending, result)
        for message in loop.adapter.result_messages(pending, result):
            ctx.add_message(message)
        await loop.events.emit(
            "tool_result", id=pending.id, name=pending.tool, ok=False,
            error="approval_interrupted", data={},
        )

    def cancel(self, session_id: str) -> bool:
        """Ask a run to stop.  Returns whether there was one."""
        loop = self._loops.get(session_id)
        if loop is None:
            return False
        loop.cancel()
        return True

    def active(self, session_id: str) -> bool:
        return session_id in self._loops


def _child_policy(mask: Any, request: RunRequest) -> Any:
    """A subagent's policy engine — always on (Batch V4-E2)."""
    from .policy import PolicyEngine, ResolvedPolicy, restrict_mode

    return PolicyEngine(ResolvedPolicy(
        mode=restrict_mode(request.session_mode, request.requested_mode),
        capabilities=mask,
        allow_mutation=not request.read_only,
    ))


def _can_delegate(policy: Any, depth: int) -> bool:
    """Whether this run should be offered ``agent.delegate`` at all."""
    from .delegation import remaining_depth

    return remaining_depth(policy, depth) > 0


def _nested_bridge(bus: Any, call: Any, agent: str) -> Any:
    """A child's event emitter, tagged so a UI can nest it under the call."""
    return _bus_bridge(bus, {
        "parent_call_id": getattr(call, "id", ""),
        "subagent": agent,
    })


def _structured_return(result: Any) -> Any:
    """Read a child ``AgentResult`` into the structured shape (Batch V4-E2)."""
    from .delegation import SubagentResult, parse_return

    outcome = parse_return(getattr(result, "answer", "") or "")
    context = getattr(result, "context", None)
    if context is not None:
        # The ledger knows which files were actually read, which beats trusting
        # the model's own list — a path it never opened is not a finding.
        touched = sorted(set(getattr(context, "files_read", set())) | set(outcome.files))
        outcome = SubagentResult(
            summary=outcome.summary,
            files=touched,
            findings=outcome.findings,
            recommendations=outcome.recommendations,
            status=outcome.status,
            note=outcome.note,
        )
    status = getattr(result, "status", "completed")
    if status not in ("completed", "degraded") and outcome.status == "completed":
        outcome.status = "partial"
        outcome.note = outcome.note or f"the subagent stopped: {status}"
    if outcome.status == "failed" and not outcome.summary:
        # The parent's model reads this and has to know what to do next. "It
        # returned nothing" is a fact; "do the work yourself" is the instruction
        # that fact implies.
        outcome.note = (
            f"{outcome.note or 'the subagent produced no result'} — carry on "
            "without it or do the work yourself"
        )
    return outcome


def _policy_enabled() -> bool:
    """Whether the real PolicyEngine runs (Batch V4-D1's flag)."""
    from ..flags import is_on

    return is_on(FLAG_AGENT_POLICY, default=False)


def _offers_todo(adapter: Any, capabilities: Any) -> bool:
    """Whether this run's model will actually be shown ``todo.write``.

    Asked of the adapter rather than the registry, because the answer depends on
    the dialect (LITE withholds it) and on the schema budget, both of which the
    adapter's render already accounts for.
    """
    try:
        return any(
            getattr(tool, "name", "") == "todo.write"
            for tool in adapter.tool_defs(capabilities)
        )
    except Exception as exc:  # noqa: BLE001 - a prompt detail, not a run blocker
        logger.debug("could not tell whether todo.write is offered: %s", exc)
        return False


def _is_read_only(policy: Any) -> bool:
    resolved = getattr(policy, "policy", None)
    return bool(getattr(resolved, "read_only", False))


def _bus_bridge(
    bus: Any, tags: Optional[Dict[str, Any]] = None,
) -> Callable[[str, Dict[str, Any]], Awaitable[None]]:
    """Render loop events onto an :class:`AgentEventBus`.

    *tags* are merged into the built event's ``data`` — after the factory, not
    before. The factories take named arguments and drop anything they were not
    written to expect, so tagging the payload on the way in silently loses it;
    that is how the first draft of subagent nesting produced no nested events.
    """

    async def emit(event_type: str, payload: Dict[str, Any]) -> None:
        factory = _EVENT_FACTORIES.get(event_type)
        if factory is not None:
            try:
                event = factory(payload)
            except Exception:  # noqa: BLE001
                # A factory that does not match its event's payload is our bug,
                # not a transport problem. The loop's emitter catches everything
                # so a dead client cannot kill a run, which would otherwise hide
                # this entirely — so it is logged loudly and the event still goes
                # out in its degraded form rather than vanishing.
                logger.warning(
                    "could not render %s event; sending it as a status change",
                    event_type, exc_info=True,
                )
            else:
                await bus.emit(_tagged(event, tags))
                return
        # No first-class renderer yet: surface it as a status change rather than
        # dropping it, so a client can already show that something happened.
        await bus.emit(_tagged(
            evt.status_change(event_type, _describe(event_type, payload)), tags,
        ))

    return emit


def _tagged(event: Any, tags: Optional[Dict[str, Any]]) -> Any:
    if tags:
        event.data.update(tags)
    return event


def _describe(event_type: str, payload: Dict[str, Any]) -> str:
    if event_type == "iteration":
        return f"step {payload.get('n')}"
    if event_type == "dialect_downgraded":
        return f"switched to the {payload.get('to')} dialect"
    if event_type == "tools_pruned":
        return f"{payload.get('count')} tool(s) withheld"
    if event_type == "run_started":
        return f"running on {payload.get('model')}"
    return ""


def _known_files(request: RunRequest) -> List[str]:
    """The file list a LITE run validates every path against."""
    if request.workspace_root is None:
        return []
    root = Path(request.workspace_root)
    try:
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, text=True, check=False, timeout=30,
        )
        listed = [line for line in result.stdout.splitlines() if line.strip()]
        if listed:
            return listed
    except Exception as exc:  # noqa: BLE001 - not a git checkout, or git absent
        logger.debug("could not list files with git (%s); walking the tree", exc)

    return [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    ][:2000]


def _mode_tool_policy(request: "RunRequest") -> Optional[Any]:
    """The active mode's tool policy, if the session named a mode — Batch V4-H2.

    ``resolve_policy`` intersects whatever it is given, so handing it the mode's
    policy is what makes a mode narrow a topology's grants. It cannot widen them:
    intersection has no direction in which that is possible.
    """
    if not request.mode_slug:
        return None
    try:
        from .system_payload import resolve_mode

        mode = resolve_mode(request.mode_slug, request.workspace_root)
    except Exception:  # noqa: BLE001 - an unusable mode leaves the mask alone
        logger.warning("could not read mode %r", request.mode_slug, exc_info=True)
        return None
    return mode.tool_policy if mode is not None else None
