# GitPilot — Public API stability contract

> **Status:** active.  Applies from version `0.3.x` onward.

The package `gitpilot.public_api` is the **only** GitPilot import surface
that the project commits to keep stable across releases.  Everything
else (every other module under `gitpilot.*`) is internal: signatures may
change, names may move, files may be deleted, all without notice.

This document explains the contract and the deprecation process.

---

## 1. What "stable" means

For every name in `gitpilot.public_api.__all__`:

| Guarantee | Detail |
|---|---|
| **The name keeps resolving** | imports never silently break |
| **The signature stays callable** | new optional parameters are fine; required ones are not added without a deprecation cycle |
| **Documented behaviour is preserved** | bug fixes are allowed, behaviour changes that contradict the docstring are not |
| **Removal goes through deprecation** | see §3 |

Anything not in `__all__` may move, be renamed, or be deleted in any release — even a patch.

---

## 2. What's on the surface today

The list is the source of truth; this section is a human-friendly summary.

* **Feature flags** — `is_on`, `set_override`, `enabled_flags`, …
* **Context** — `AgentsLoader`, `MentionParser`, `ContextBudgetManager`,
  `build_context_cached`
* **Tools** — `ToolPolicy`, `EditGuard`, `MCPGuard`, `classify_tool`,
  `register_tool_category`, `prune_descriptors`, `MCPServerToggles`,
  `MCPToggleRegistry`, `validate_tool_output`
* **Modes** — `Mode`, `ModeRegistry`, `activate_mode`, `ActiveModeContext`
* **Slash commands** — `SlashCommand`, `SlashCommandRegistry`
* **Checkpoints** — `CheckpointStore`, `CheckpointRecord`,
  `ToolCallDescriptor`
* **Rules** — `Rule`, `RuleSet`, `compose_rules`, `load_rules`
* **Sandbox** — `get_sandbox`, `SandboxPolicy`, `SandboxResult`,
  `NullSandbox`, `SubprocessSandbox`, `MatrixLabSandbox`,
  `SandboxError`, `SandboxUnavailableError`, `SandboxRunError`,
  `BACKEND_OFF`, `BACKEND_SUBPROCESS`, `BACKEND_MATRIXLAB`
* **Trust** — `TrustStore`, `TrustEntry`, `TrustStatus`,
  `workspace_fingerprint`
* **Errors** — `GitPilotError`, `NotFoundError`, `UpstreamError`,
  `ValidationError`, `wrap_errors_envelope`, `error_envelope`,
  `error_envelope_response`
* **Doctor** — `doctor_run_checks`, `doctor_render_text`,
  `doctor_render_json`, `CheckResult`, `DoctorReport`
* **Prompt cache (Phase 2)** — `build_system_blocks`,
  `to_anthropic_kwargs`, `to_legacy_system_string`, `SystemPayload`,
  `SystemBlock`, `PromptCacheProvider`
* **Streaming (Phase 2)** — `register_stream_routes`,
  `AgentStreamRunner`, `StreamEvent`, `StreamMetrics`,
  `format_sse_event`, `stream_fallback_adapter`
* **Context cache (Phase 2)** — `build_context_cached`,
  `get_context_cache_stats`, `clear_context_cache`, `ContextCacheStats`
* **Warmup (Phase 2)** — `register_warmup`, `run_warmup_async`,
  `run_warmup_now`, `WarmupResult`
* **Wizard (Phase 3)** — `run_wizard`, `WizardAnswers`,
  `WizardResult`, `WizardError`, `WizardPrompter`, `ScriptedPrompter`,
  `wizard_render_env`, `wizard_render_modes`,
  `supported_provider_slugs`, `starter_mode_slugs`
* **Deprecation infra** — `deprecated_alias`

The authoritative list is in `gitpilot/public_api/__init__.py`.
A CI test (`tests/test_public_api.py`) fails if any name in `__all__`
becomes unimportable.

---

## 3. Deprecation process

When a public name needs to go, this is the path:

1. **Announce** in the release that introduces the deprecation:
   ```
   parse_mentions  →  use expand_mentions instead.
                       Scheduled for removal in v2.0.
   ```
2. **Wrap** the symbol with `deprecated_alias` so the first call per
   process emits a `DeprecationWarning`:

   ```python
   from gitpilot._deprecation import deprecated_alias
   parse_mentions = deprecated_alias(
       "parse_mentions", expand_mentions,
       replacement="gitpilot.public_api.expand_mentions",
       removed_in="2.0",
   )
   ```

3. **Keep** the symbol working for at least one minor release.
4. **Remove** only on the milestone version named in `removed_in`.

The `deprecated_alias` helper enforces:

* fixed-format warning text (`<old> is deprecated; use <new> instead
  (will be removed in v<X.Y>)`)
* emit-once-per-process semantics (no log spam)
* a `__gitpilot_deprecated__` metadata attribute on the wrapper, so
  documentation generators and migration tooling can find every
  deprecated name without parsing source

Callers that want to opt out of the noise can filter the category as
usual:

```python
import warnings
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module=r"gitpilot\..*",
)
```

---

## 4. SemVer mapping

GitPilot follows semantic versioning for the `public_api` surface only:

* **MAJOR** — a name is removed, or a required parameter is added.
* **MINOR** — a new name lands, a deprecation is announced, or a new
  optional parameter is added.
* **PATCH** — bug fixes and behaviour preserved.

Internal modules ignore SemVer entirely.

---

## 5. Suggested migration playbook for callers

If you are integrating GitPilot inside another tool, do exactly two
things to stay future-proof:

1.  **Import only from `gitpilot.public_api`.**  Reaching into
    `gitpilot.session` or `gitpilot.agent_executor` is allowed but
    not protected.
2.  **Treat any `DeprecationWarning` from `gitpilot._deprecation` as
    a hard build break.**  CI:

    ```bash
    pytest -W error::DeprecationWarning
    ```

Following both ensures one GitPilot major-bump is the only place you
need to spend migration effort.
