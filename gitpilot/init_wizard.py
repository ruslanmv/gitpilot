# gitpilot/init_wizard.py
"""First-run wizard — Batch P3-G.

Walks a new user through the four decisions that previously required
reading three pages of documentation:

1.  Pick a model provider (Anthropic, OpenAI, Ollama, Watsonx).
2.  Supply the matching API key (skipped for local-only providers).
3.  Pick a starter mode (``coder``, ``planner``, ``reviewer``).
4.  Trust the current workspace (records it in
    :class:`gitpilot.trusted_folders.TrustStore`).

Output artefacts, all written atomically:

* ``.env``                         — only the keys the user actually picked
* ``.gitpilot/modes.yaml``         — one starter mode for the selection
* ``AGENTS.md``                    — via :func:`gitpilot.agents_md.run_init`
* trust entry in ``~/.gitpilot/trusted.json``

Design rules
------------
* **Atomic** — every file is written to a sibling temp file, fsynced,
  then renamed.  An abort (Ctrl-C, KeyboardInterrupt, validation
  error) leaves the workspace untouched.
* **Secret-safe** — the wizard never echoes the API key back to stdout.
  Confirmation messages report ``set`` / ``not set`` only.
* **Idempotent** — re-running the wizard with the same answers
  produces a byte-identical ``.env`` and ``.gitpilot/modes.yaml``.  An
  existing file is preserved by default; ``overwrite=True`` is opt-in.
* **Non-interactive friendly** — every prompt can be pre-answered via
  the :class:`WizardAnswers` dataclass so the wizard runs in CI and
  scripts without TTY access.
* **Flag-gated** — public entry points consult ``init_wizard``.  With
  the flag off the function refuses to run, leaving manual ``init``
  intact.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
)

from . import flags
from .agents_md import run_init as run_agents_md_init
from .trusted_folders import TrustStore

logger = logging.getLogger(__name__)

FLAG_INIT_WIZARD = "init_wizard"
SECRET_REDACTED = "***"


# ----------------------------------------------------------------------
# Catalog of providers
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class _ProviderSpec:
    slug: str          # canonical lowercase id
    label: str         # display name
    env_key: Optional[str]   # secret env var (None for local providers)
    default_model: str
    notes: str

    @property
    def needs_key(self) -> bool:
        return self.env_key is not None


SUPPORTED_PROVIDERS: Tuple[_ProviderSpec, ...] = (
    _ProviderSpec("anthropic", "Anthropic Claude", "ANTHROPIC_API_KEY",
                  "claude-sonnet-4-5", "Default for hosted use."),
    _ProviderSpec("openai", "OpenAI", "OPENAI_API_KEY",
                  "gpt-4o-mini", ""),
    _ProviderSpec("watsonx", "IBM watsonx", "WATSONX_API_KEY",
                  "meta-llama/llama-3-1-8b-instruct",
                  "Set WATSONX_PROJECT_ID separately."),
    _ProviderSpec("ollama", "Ollama (local)", None,
                  "llama3.1", "Runs locally; no key needed."),
)


def provider_by_slug(slug: str) -> Optional[_ProviderSpec]:
    s = slug.strip().lower()
    for prov in SUPPORTED_PROVIDERS:
        if prov.slug == s:
            return prov
    return None


# ----------------------------------------------------------------------
# Starter modes
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class _ModeSpec:
    slug: str
    label: str
    role: str
    when: str
    groups: Tuple[Any, ...]


STARTER_MODES: Tuple[_ModeSpec, ...] = (
    _ModeSpec(
        slug="coder",
        label="Coder",
        role=("You write code, run tests, and self-correct on failure. "
              "Keep changes small and reversible."),
        when="Use to implement features and fix bugs.",
        groups=("read", "edit", "command"),
    ),
    _ModeSpec(
        slug="planner",
        label="Planner",
        role=("You explore the repo and draft step-by-step plans with risks "
              "and acceptance criteria.  You never write code yourself."),
        when="Use before implementing a complex change.",
        groups=("read",),
    ),
    _ModeSpec(
        slug="reviewer",
        label="Reviewer",
        role=("You audit diffs, suggest improvements, and draft commit "
              "messages.  You never modify the working tree."),
        when="Use after a change is ready, before commit.",
        groups=("read",),
    ),
)


def mode_by_slug(slug: str) -> Optional[_ModeSpec]:
    s = slug.strip().lower()
    for mode in STARTER_MODES:
        if mode.slug == s:
            return mode
    return None


# ----------------------------------------------------------------------
# Answers + result
# ----------------------------------------------------------------------

@dataclass
class WizardAnswers:
    """Inputs collected from the user (or supplied non-interactively)."""

    provider: str = "anthropic"
    api_key: Optional[str] = None       # ``None`` for providers without a key
    mode_slug: str = "coder"
    workspace_trust: bool = True
    overwrite_env: bool = False
    overwrite_modes: bool = False
    overwrite_agents_md: bool = False


@dataclass
class WizardResult:
    """Outcome of one wizard run."""

    workspace: Path
    files_written: List[Path] = field(default_factory=list)
    files_skipped: List[Tuple[Path, str]] = field(default_factory=list)
    trust_recorded: bool = False
    provider: str = ""
    mode_slug: str = ""
    duration_ms: int = 0
    aborted: bool = False
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "files_written": [str(p) for p in self.files_written],
            "files_skipped": [(str(p), why) for p, why in self.files_skipped],
            "trust_recorded": self.trust_recorded,
            "provider": self.provider,
            "mode_slug": self.mode_slug,
            "duration_ms": self.duration_ms,
            "aborted": self.aborted,
            "reason": self.reason,
        }


class WizardError(Exception):
    """Surfaced when validation fails before any file is written."""


# ----------------------------------------------------------------------
# Prompt protocols (so tests can drive the wizard without a TTY)
# ----------------------------------------------------------------------

class Prompter:
    """Tiny abstraction over Typer prompts.  Implementations are simple
    enough to swap for a recorded transcript in tests."""

    def text(self, message: str, *, default: Optional[str] = None) -> str:
        raise NotImplementedError

    def secret(self, message: str) -> str:
        raise NotImplementedError

    def select(self, message: str, options: List[str], *, default: int = 0) -> int:
        raise NotImplementedError

    def confirm(self, message: str, *, default: bool = True) -> bool:
        raise NotImplementedError

    def echo(self, message: str = "") -> None:
        raise NotImplementedError


class _TyperPrompter(Prompter):
    """Real prompts backed by Typer / Rich.  Imported lazily."""

    def __init__(self) -> None:
        import typer  # local
        self._typer = typer

    def text(self, message: str, *, default: Optional[str] = None) -> str:
        return str(self._typer.prompt(message, default=default or ""))

    def secret(self, message: str) -> str:
        return str(self._typer.prompt(
            message, hide_input=True, default="", show_default=False,
        ))

    def select(self, message: str, options: List[str], *, default: int = 0) -> int:
        self.echo(message)
        for i, opt in enumerate(options):
            self.echo(f"  [{i + 1}] {opt}")
        while True:
            raw = self._typer.prompt("Choose", default=str(default + 1))
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return idx
            except ValueError:
                pass
            self.echo(f"Please enter a number between 1 and {len(options)}.")

    def confirm(self, message: str, *, default: bool = True) -> bool:
        return self._typer.confirm(message, default=default)

    def echo(self, message: str = "") -> None:
        self._typer.echo(message)


@dataclass
class ScriptedPrompter(Prompter):
    """Prompter driven by a list of pre-recorded answers.  Test-only."""

    answers: List[Any]
    echoed: List[str] = field(default_factory=list)
    _cursor: int = 0

    def _pop(self) -> Any:
        if self._cursor >= len(self.answers):
            raise WizardError("scripted prompter ran out of answers")
        value = self.answers[self._cursor]
        self._cursor += 1
        return value

    def text(self, message: str, *, default: Optional[str] = None) -> str:
        return str(self._pop())

    def secret(self, message: str) -> str:
        return str(self._pop())

    def select(self, message: str, options: List[str], *, default: int = 0) -> int:
        value = self._pop()
        if isinstance(value, int):
            return value
        # Strings can pass either the slug or the label
        s = str(value).strip().lower()
        for i, opt in enumerate(options):
            if opt.lower() == s:
                return i
        raise WizardError(f"scripted option {value!r} not in {options}")

    def confirm(self, message: str, *, default: bool = True) -> bool:
        return bool(self._pop())

    def echo(self, message: str = "") -> None:
        self.echoed.append(message)


# ----------------------------------------------------------------------
# Core runner
# ----------------------------------------------------------------------

def collect_answers(
    *,
    prompter: Prompter,
    presets: Optional[WizardAnswers] = None,
) -> WizardAnswers:
    """Drive the interactive prompts.  ``presets`` short-circuits any
    field that is already set (anything not ``None``)."""
    presets = presets or WizardAnswers()
    prompter.echo("== GitPilot first-run wizard ==")

    # 1. Provider
    options = [f"{p.label}" for p in SUPPORTED_PROVIDERS]
    chosen_idx = next(
        (i for i, p in enumerate(SUPPORTED_PROVIDERS) if p.slug == presets.provider),
        0,
    )
    idx = prompter.select("Which model provider?", options, default=chosen_idx)
    provider_spec = SUPPORTED_PROVIDERS[idx]

    # 2. API key (if needed and not pre-supplied)
    api_key: Optional[str] = presets.api_key
    if provider_spec.needs_key:
        if api_key is None:
            api_key = prompter.secret(f"Paste your {provider_spec.env_key}").strip()
        if not api_key:
            raise WizardError(
                f"{provider_spec.env_key} is required for the {provider_spec.label} provider."
            )

    # 3. Starter mode
    mode_options = [f"{m.label} — {m.when}" for m in STARTER_MODES]
    mode_idx = next(
        (i for i, m in enumerate(STARTER_MODES) if m.slug == presets.mode_slug),
        0,
    )
    selected_mode = prompter.select(
        "Starter mode?", mode_options, default=mode_idx,
    )
    mode_spec = STARTER_MODES[selected_mode]

    # 4. Workspace trust
    workspace_trust = prompter.confirm(
        "Trust this workspace (allow tool execution)?",
        default=presets.workspace_trust,
    )

    return WizardAnswers(
        provider=provider_spec.slug,
        api_key=api_key,
        mode_slug=mode_spec.slug,
        workspace_trust=workspace_trust,
        overwrite_env=presets.overwrite_env,
        overwrite_modes=presets.overwrite_modes,
        overwrite_agents_md=presets.overwrite_agents_md,
    )


def run_wizard(
    workspace: Path,
    *,
    prompter: Optional[Prompter] = None,
    presets: Optional[WizardAnswers] = None,
    trust_store: Optional[TrustStore] = None,
    enabled: Optional[bool] = None,
) -> WizardResult:
    """Run the full wizard end-to-end and return a :class:`WizardResult`.

    Raises :class:`WizardError` for validation failures *before* any
    file is touched.  Mid-run aborts (Ctrl-C, partial writes) leave the
    workspace untouched thanks to :func:`_atomic_write`.
    """
    flag_on = enabled if enabled is not None else flags.is_on(FLAG_INIT_WIZARD)
    if not flag_on:
        raise WizardError(
            "init_wizard flag is off; run `gitpilot init` for the legacy flow."
        )

    start = time.monotonic()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    result = WizardResult(workspace=workspace)

    # Phase 0 — validate any presets that we *can* validate before
    # touching prompts.  A typed-but-unknown provider/mode in the
    # presets is a clean abort, not a fall-through to prompts.
    if presets is not None:
        if provider_by_slug(presets.provider) is None:
            result.aborted = True
            result.reason = f"unsupported provider: {presets.provider!r}"
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result
        if mode_by_slug(presets.mode_slug) is None:
            result.aborted = True
            result.reason = f"unsupported mode: {presets.mode_slug!r}"
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result

    # Phase 1 — collect (no writes yet)
    try:
        prompter = prompter or _TyperPrompter()
        if presets and _is_complete(presets):
            answers = presets
        else:
            answers = collect_answers(prompter=prompter, presets=presets)
    except KeyboardInterrupt:
        result.aborted = True
        result.reason = "user aborted"
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    result.provider = answers.provider
    result.mode_slug = answers.mode_slug

    # Phase 2 — render in-memory artefacts
    env_text = _render_env(answers)
    modes_text = _render_modes(answers)

    # Phase 3 — write atomically (rollback any partial writes on failure)
    rollback_handlers: List[Callable[[], None]] = []
    try:
        env_path = workspace / ".env"
        if env_path.exists() and not answers.overwrite_env:
            result.files_skipped.append((env_path, "exists"))
        else:
            _atomic_write(env_path, env_text, mode=0o600,
                          rollback=rollback_handlers)
            result.files_written.append(env_path)

        gitpilot_dir = workspace / ".gitpilot"
        gitpilot_dir.mkdir(exist_ok=True)
        modes_path = gitpilot_dir / "modes.yaml"
        if modes_path.exists() and not answers.overwrite_modes:
            result.files_skipped.append((modes_path, "exists"))
        else:
            _atomic_write(modes_path, modes_text, mode=0o644,
                          rollback=rollback_handlers)
            result.files_written.append(modes_path)

        agents_md_path = workspace / "AGENTS.md"
        if agents_md_path.exists() and not answers.overwrite_agents_md:
            result.files_skipped.append((agents_md_path, "exists"))
        else:
            report = run_agents_md_init(workspace, overwrite=answers.overwrite_agents_md)
            if report.created:
                result.files_written.append(agents_md_path)

                def _agents_rollback(p: Path = agents_md_path) -> None:
                    _unlink_quiet(p)

                rollback_handlers.append(_agents_rollback)
            else:
                result.files_skipped.append((agents_md_path, report.skipped_reason or "exists"))

        if answers.workspace_trust:
            store = trust_store or TrustStore.default()
            store.trust(workspace, note="set up via wizard")
            result.trust_recorded = True

    except Exception as exc:
        # Atomic rollback — undo any successful writes so the user can
        # safely re-run.  We log the error rather than re-raise so the
        # WizardResult always describes what happened.
        for fn in reversed(rollback_handlers):
            try:
                fn()
            except Exception:
                logger.exception("rollback handler failed")
        result.aborted = True
        result.reason = str(exc) or exc.__class__.__name__
        result.files_written = []
        logger.exception("wizard failed")

    result.duration_ms = int((time.monotonic() - start) * 1000)
    return result


# ----------------------------------------------------------------------
# Renderers — pure functions, easy to snapshot in tests
# ----------------------------------------------------------------------

def _render_env(answers: WizardAnswers) -> str:
    spec = provider_by_slug(answers.provider)
    if spec is None:
        raise WizardError(f"unsupported provider: {answers.provider!r}")
    lines: List[str] = [
        "# GitPilot environment — generated by `gitpilot init --wizard`.",
        "# Only the keys you actually need are listed; add more as required.",
        f"GITPILOT_LLM_PROVIDER={spec.slug}",
        f"GITPILOT_DEFAULT_MODEL={spec.default_model}",
    ]
    if spec.needs_key:
        if not answers.api_key:
            raise WizardError(f"{spec.env_key} is required")
        _validate_env_value(answers.api_key)
        lines.append(f"{spec.env_key}={answers.api_key}")
    return "\n".join(lines) + "\n"


def _render_modes(answers: WizardAnswers) -> str:
    spec = mode_by_slug(answers.mode_slug)
    if spec is None:
        raise WizardError(f"unsupported mode: {answers.mode_slug!r}")
    groups_yaml = "\n".join(f"      - {g}" for g in spec.groups)
    return (
        "# GitPilot modes — generated by `gitpilot init --wizard`.\n"
        "# Edit freely; new modes can be added under customModes.\n"
        "customModes:\n"
        f"  - slug: {spec.slug}\n"
        f"    name: {spec.label}\n"
        f"    description: {spec.label} starter mode\n"
        f"    roleDefinition: |\n"
        f"      {spec.role}\n"
        f"    whenToUse: |\n"
        f"      {spec.when}\n"
        "    groups:\n"
        f"{groups_yaml}\n"
    )


# ----------------------------------------------------------------------
# Safety helpers
# ----------------------------------------------------------------------

_FORBIDDEN_ENV_CHARS = re.compile(r"[\r\n\x00]")


def _validate_env_value(value: str) -> None:
    """Reject newlines and NULs so the secret can't break out of the file."""
    if _FORBIDDEN_ENV_CHARS.search(value):
        raise WizardError("API key contains forbidden control characters")


def _atomic_write(
    path: Path,
    text: str,
    *,
    mode: int = 0o644,
    rollback: List[Callable[[], None]],
) -> None:
    """Write *text* to *path* atomically.

    The file is written to a sibling temp file in the same directory,
    fsynced for durability, then renamed over the target.  A rollback
    handler that deletes the renamed file is appended to *rollback*
    so the wizard can undo all writes on a later failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        _unlink_quiet(tmp_path)
        raise

    def _undo(p: Path = path) -> None:
        _unlink_quiet(p)

    rollback.append(_undo)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.exception("could not unlink %s", path)


def _is_complete(answers: WizardAnswers) -> bool:
    """True if presets cover every prompt — wizard runs non-interactively."""
    spec = provider_by_slug(answers.provider)
    if spec is None:
        return False
    if spec.needs_key and not answers.api_key:
        return False
    return mode_by_slug(answers.mode_slug) is not None


# ----------------------------------------------------------------------
# Rendering helpers exported for tests
# ----------------------------------------------------------------------

def render_env(answers: WizardAnswers) -> str:
    """Public render helper for snapshot tests."""
    return _render_env(answers)


def render_modes(answers: WizardAnswers) -> str:
    """Public render helper for snapshot tests."""
    return _render_modes(answers)


def supported_provider_slugs() -> List[str]:
    """Return the canonical slug for each provider the wizard supports."""
    return [p.slug for p in SUPPORTED_PROVIDERS]


def starter_mode_slugs() -> List[str]:
    """Return the slug for each starter mode the wizard can write."""
    return [m.slug for m in STARTER_MODES]


# ----------------------------------------------------------------------
# Module-level entry — ``python -m gitpilot.init_wizard --provider …``
# ----------------------------------------------------------------------

def _module_main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - manual
    import argparse

    parser = argparse.ArgumentParser(prog="gitpilot.init_wizard")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--mode", default="coder")
    parser.add_argument("--no-trust", action="store_true")
    args = parser.parse_args(argv)
    presets = WizardAnswers(
        provider=args.provider,
        api_key=args.api_key,
        mode_slug=args.mode,
        workspace_trust=not args.no_trust,
    )
    flags.set_override(FLAG_INIT_WIZARD, True)
    result = run_wizard(args.workspace, presets=presets,
                        prompter=ScriptedPrompter(answers=[]))
    import json
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if not result.aborted else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_module_main())
