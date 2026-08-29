# gitpilot/agent/model_profile.py
"""What a model can do, and therefore how it is spoken to — Batch V4-B2.

This is the module that makes "works on low-end and high-end models" an
architectural property rather than a test matrix.  One :class:`ModelProfile` per
(provider, model) decides the dialect, the context window, how many tool schemas
to show and at what verbosity, whether reasoning tags need stripping, and
whether the prompt prefix can be cached.

Resolution order, first match wins:

1. **``.gitpilot/models.yaml``** — an explicit override, so a model nobody has
   catalogued can be pinned without waiting for a release.
2. **Built-in tables** — seeded from what the codebase already knows:
   ``agentic._INCOMPATIBLE_MODEL_PATTERNS`` (the <7B and reasoning list that
   already routes to Lite Mode), ``reasoning_normalizer.REASONING_MODEL_PATTERNS``,
   ``context_meter``'s window tables, and provider families.
3. **Runtime probe** — one cheap tool-call request against an unknown model,
   cached in ``~/.gitpilot/model_profiles.json``.  Flag ``model_probe``.

The important consequence of that ordering: for any model the tables cover, the
probe never runs.  A test that asserts "the probe classifies qwen2.5:1.5b" would
be asserting nothing, which is why the probe is tested with ids absent from
every table.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse

from .contracts import Dialect

logger = logging.getLogger(__name__)

FLAG_MODEL_PROFILES = "model_profiles"
FLAG_MODEL_PROBE = "model_probe"

PROFILE_CACHE_PATH = Path.home() / ".gitpilot" / "model_profiles.json"
MODELS_YAML_REL = ".gitpilot/models.yaml"

#: Providers whose chat APIs take tool definitions natively.  ``ollama`` is
#: absent on purpose: some Ollama models do support tools and many do not, and
#: the model — not the transport — is what decides, so those fall to the tables
#: and then the probe.
NATIVE_PROVIDER_FAMILIES = frozenset({"openai", "claude", "anthropic"})

#: Schema budgets by dialect.  A 1.5B model handed forty JSON Schemas has no
#: context left for the task; a frontier model is unbothered by them.
DEFAULT_MAX_TOOL_SCHEMAS = {
    Dialect.NATIVE: 64,
    Dialect.REACT_TEXT: 16,
    Dialect.LITE: 6,
}

DEFAULT_VERBOSITY = {
    Dialect.NATIVE: "full",
    Dialect.REACT_TEXT: "compact",
    Dialect.LITE: "names_only",
}

#: The degradation ladder, most capable first.  Used by :meth:`ModelProfile.forced`
#: to tell a deliberate pin (downwards) from a claim the profile contradicts.
_DIALECT_RANK = {Dialect.NATIVE: 0, Dialect.REACT_TEXT: 1, Dialect.LITE: 2}

#: Below this, a model cannot hold a full JSON Schema list plus a task.
SMALL_CONTEXT_THRESHOLD = 16_000


@dataclass(frozen=True)
class ModelProfile:
    """A resolved answer to "what can this model do?"."""

    provider: str
    model: str
    dialect: Dialect
    context_window: int = 8192
    max_tool_schemas: int = 16
    schema_verbosity: str = "compact"
    supports_parallel_tool_calls: bool = False
    supports_streaming: bool = True
    strip_reasoning_tags: bool = False
    prompt_style: str = "lean"          # "standard" | "lean" (agent_prompts budgets)
    cacheable_prefix: bool = False
    source: str = "table"               # "yaml" | "table" | "probe" | "fallback"
    notes: str = ""

    def downgraded(self) -> "ModelProfile":
        """The next rung down the degradation ladder (§6.6).

        One-way and mid-run only: a model that produced malformed tool calls
        twice gets a slower dialect rather than a broken session.  LITE has no
        rung below it, so it returns itself.
        """
        if self.dialect is Dialect.NATIVE:
            nxt = Dialect.REACT_TEXT
        elif self.dialect is Dialect.REACT_TEXT:
            nxt = Dialect.LITE
        else:
            return self
        return replace(
            self,
            dialect=nxt,
            max_tool_schemas=min(self.max_tool_schemas, DEFAULT_MAX_TOOL_SCHEMAS[nxt]),
            schema_verbosity=DEFAULT_VERBOSITY[nxt],
            supports_parallel_tool_calls=False,
            source=f"{self.source}+downgraded",
            notes=f"downgraded from {self.dialect.value} after malformed output",
        )

    def forced(self, dialect: Dialect) -> "ModelProfile":
        """This profile, spoken to in *dialect* — Batch V4-G1.

        A topology may pin a dialect (``execution.dialect: lite``, §15.1/§6.4),
        which is how T8 stops being a separate planner universe. The pin may only
        move *down* the ladder: forcing LITE on a frontier model is a deliberate
        choice, while forcing NATIVE on a model whose profile says it has no
        native tool calling would produce a run where every call comes back
        malformed. An upgrade is refused with a warning rather than obeyed,
        because a topology is configuration and the profile is measurement.
        """
        if dialect is self.dialect:
            return self
        if _DIALECT_RANK[dialect] < _DIALECT_RANK[self.dialect]:
            logger.warning(
                "topology asked for the %s dialect but %s resolves to %s; keeping %s",
                dialect.value, self.model, self.dialect.value, self.dialect.value,
            )
            return self
        return replace(
            self,
            dialect=dialect,
            max_tool_schemas=min(self.max_tool_schemas, DEFAULT_MAX_TOOL_SCHEMAS[dialect]),
            schema_verbosity=DEFAULT_VERBOSITY[dialect],
            supports_parallel_tool_calls=(
                self.supports_parallel_tool_calls and dialect is Dialect.NATIVE
            ),
            source=f"{self.source}+pinned",
            notes=f"dialect pinned to {dialect.value} by the topology",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dialect": self.dialect.value,
            "context_window": self.context_window,
            "max_tool_schemas": self.max_tool_schemas,
            "schema_verbosity": self.schema_verbosity,
            "supports_parallel_tool_calls": self.supports_parallel_tool_calls,
            "supports_streaming": self.supports_streaming,
            "strip_reasoning_tags": self.strip_reasoning_tags,
            "prompt_style": self.prompt_style,
            "cacheable_prefix": self.cacheable_prefix,
            "source": self.source,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Table lookups
# ---------------------------------------------------------------------------


def _normalise_model(model: str) -> str:
    """Lowercase and drop any provider prefix (``ollama/llama3`` → ``llama3``)."""
    name = str(model or "").strip().lower()
    if "/" in name:
        name = name.split("/", 1)[-1]
    return name


def is_small_or_reasoning_model(model: str) -> bool:
    """Whether the model is on the list Lite Mode already exists for.

    Reuses ``agentic._INCOMPATIBLE_MODEL_PATTERNS`` rather than copying it: that
    table is maintained by whoever hits a model that cannot follow a multi-agent
    ReAct prompt, and a second copy would drift within a release.
    """
    from ..agentic import _INCOMPATIBLE_MODEL_PATTERNS

    name = _normalise_model(model)
    return any(pattern in name for pattern in _INCOMPATIBLE_MODEL_PATTERNS)


def is_reasoning_model(model: str) -> bool:
    from ..reasoning_normalizer import is_reasoning_model as _is_reasoning

    return bool(_is_reasoning(model or ""))


def _longest_prefix_window(name: str, table: Mapping[str, int], default: int) -> int:
    """Exact match, else the longest matching prefix, else *default*.

    Providers append dates and variants to a model id (``gpt-4o-2024-08-06``,
    ``o1-preview``, ``claude-3-5-sonnet-20241022``).  An exact-match lookup
    silently returns the 8k default for those, which then makes the loop compact
    a 200k-window conversation — so the prefix rule that ``context_meter``
    already applies to Ollama tags applies here too.  Longest wins, so ``o1-mini``
    is not shadowed by ``o1``.
    """
    if name in table:
        return table[name]
    best_key = ""
    for key in table:
        if name.startswith(key) and len(key) > len(best_key):
            best_key = key
    return table[best_key] if best_key else default


def resolve_context_window(provider: str, model: str) -> int:
    """Window size from ``context_meter``'s tables.

    ``context_meter.resolve_context_window`` takes settings rather than a
    (provider, model) pair, so its internal tables are consulted directly — the
    numbers stay in one place even though the entry point does not fit.
    """
    from .. import context_meter

    name = _normalise_model(model)
    family = (provider or "").strip().lower()

    if family in ("openai",):
        return _longest_prefix_window(
            name, context_meter._OPENAI_WINDOWS, context_meter._DEFAULT_CONTEXT_WINDOW,
        )
    if family in ("claude", "anthropic"):
        return _longest_prefix_window(name, context_meter._CLAUDE_WINDOWS, 200_000)
    if family == "watsonx":
        return context_meter._WATSONX_WINDOWS.get(name, context_meter._DEFAULT_CONTEXT_WINDOW)
    if family in ("ollama", "ollabridge", "openwebui", "custom"):
        return context_meter._ollama_window(name)
    return context_meter._DEFAULT_CONTEXT_WINDOW


def dialect_from_tables(provider: str, model: str) -> Optional[Dialect]:
    """The dialect the built-in tables imply, or ``None`` if they do not know.

    ``None`` is the signal to probe.  Ordering matters: the small/reasoning list
    wins over the provider family, so ``o1-`` on OpenAI lands in LITE rather
    than NATIVE — those models break a tool-calling loop in a way the family
    rule cannot see.
    """
    if is_small_or_reasoning_model(model):
        return Dialect.LITE
    if (provider or "").strip().lower() in NATIVE_PROVIDER_FAMILIES:
        return Dialect.NATIVE
    return None


# ---------------------------------------------------------------------------
# models.yaml override
# ---------------------------------------------------------------------------


def load_yaml_overrides(workspace: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Read ``.gitpilot/models.yaml``, keyed by lowercased model name.

    Uses the in-tree YAML subset parser that ``modes.py`` uses, so this needs no
    new dependency and behaves the same way for the same file shapes.

    Expected shape::

        models:
          my-finetune:7b:
            dialect: react_text
            context_window: 32768
            max_tool_schemas: 12
    """
    root = Path(workspace) if workspace else Path.cwd()
    path = root / MODELS_YAML_REL
    if not path.exists():
        return {}

    try:
        from ..modes import _load_yaml_or_json

        data = _load_yaml_or_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a malformed override must not break a run
        logger.warning("could not read %s: %s", path, exc)
        return {}

    entries = (data or {}).get("models") or {}
    if not isinstance(entries, dict):
        return {}
    return {
        _normalise_model(name): value
        for name, value in entries.items()
        if isinstance(value, dict)
    }


def _apply_override(profile: ModelProfile, override: Dict[str, Any]) -> ModelProfile:
    fields: Dict[str, Any] = {}

    raw_dialect = override.get("dialect")
    if raw_dialect:
        try:
            dialect = Dialect(str(raw_dialect).strip().lower())
        except ValueError:
            logger.warning("unknown dialect %r in models.yaml; ignoring", raw_dialect)
        else:
            fields["dialect"] = dialect
            fields.setdefault("max_tool_schemas", DEFAULT_MAX_TOOL_SCHEMAS[dialect])
            fields.setdefault("schema_verbosity", DEFAULT_VERBOSITY[dialect])

    for key in (
        "context_window", "max_tool_schemas", "schema_verbosity",
        "supports_parallel_tool_calls", "supports_streaming",
        "strip_reasoning_tags", "prompt_style", "cacheable_prefix",
    ):
        if key in override and override[key] is not None:
            fields[key] = override[key]

    fields["source"] = "yaml"
    return replace(profile, **fields)


# ---------------------------------------------------------------------------
# Probe cache
# ---------------------------------------------------------------------------


def _cache_key(base_url: str, model: str) -> str:
    """Keyed on host, not full URL: the same Ollama reached as ``localhost`` and
    as ``127.0.0.1`` is one server, but a different host is a different fleet and
    may serve a different model behind the same name."""
    host = ""
    if base_url:
        parsed = urlparse(base_url if "//" in base_url else f"//{base_url}")
        host = parsed.netloc or parsed.path
    return f"{host or 'local'}::{_normalise_model(model)}"


def read_probe_cache(path: Path = PROFILE_CACHE_PATH) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt cache is not worth failing over
        return {}
    return data if isinstance(data, dict) else {}


def write_probe_cache(
    key: str, entry: Dict[str, Any], path: Path = PROFILE_CACHE_PATH,
) -> None:
    cache = read_probe_cache(path)
    cache[key] = entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.debug("could not persist the model-profile cache: %s", exc)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def base_profile(provider: str, model: str, dialect: Dialect, source: str) -> ModelProfile:
    """Fill in everything that follows from (provider, model, dialect)."""
    window = resolve_context_window(provider, model)
    family = (provider or "").strip().lower()

    max_schemas = DEFAULT_MAX_TOOL_SCHEMAS[dialect]
    if window < SMALL_CONTEXT_THRESHOLD:
        # A small window cannot hold a long tool list whatever the dialect.
        max_schemas = min(max_schemas, 8)

    return ModelProfile(
        provider=family,
        model=model,
        dialect=dialect,
        context_window=window,
        max_tool_schemas=max_schemas,
        schema_verbosity=DEFAULT_VERBOSITY[dialect],
        # Only claimed where it has been verified; watsonx and local models
        # fall to serial calls, which every model can do.
        supports_parallel_tool_calls=dialect is Dialect.NATIVE
        and family in NATIVE_PROVIDER_FAMILIES,
        supports_streaming=family != "watsonx",
        strip_reasoning_tags=is_reasoning_model(model),
        prompt_style="standard" if dialect is Dialect.NATIVE else "lean",
        cacheable_prefix=family in ("claude", "anthropic"),
        source=source,
    )


def resolve_profile(
    provider: str,
    model: str,
    *,
    workspace: Optional[Path] = None,
    base_url: str = "",
    cache_path: Path = PROFILE_CACHE_PATH,
    allow_probe: bool = True,
) -> ModelProfile:
    """Resolve a profile without performing I/O against the model.

    The probe is a separate, awaitable step (:func:`probe_model`) because
    resolution happens on every run and a network round-trip per run would be
    unacceptable; the cache is consulted here so a probed answer is free
    thereafter.
    """
    overrides = load_yaml_overrides(workspace)
    override = overrides.get(_normalise_model(model))

    table_dialect = dialect_from_tables(provider, model)

    if table_dialect is None and allow_probe:
        cached = read_probe_cache(cache_path).get(_cache_key(base_url, model))
        if cached and cached.get("dialect"):
            try:
                table_dialect = Dialect(str(cached["dialect"]))
                source = "probe"
            except ValueError:
                table_dialect, source = None, "fallback"
            else:
                profile = base_profile(provider, model, table_dialect, source)
                return _apply_override(profile, override) if override else profile

    if table_dialect is None:
        # Unknown and unprobed.  REACT_TEXT is the safe middle: it needs no
        # native tool API and, unlike LITE, keeps the full tool surface.
        profile = base_profile(provider, model, Dialect.REACT_TEXT, "fallback")
        profile = replace(
            profile,
            notes="no table entry and no cached probe; assuming a text grammar",
        )
    else:
        profile = base_profile(provider, model, table_dialect, "table")

    return _apply_override(profile, override) if override else profile


def needs_probe(provider: str, model: str, *, base_url: str = "",
                cache_path: Path = PROFILE_CACHE_PATH) -> bool:
    """Whether a probe would tell us anything the tables and cache cannot."""
    if dialect_from_tables(provider, model) is not None:
        return False
    return _cache_key(base_url, model) not in read_probe_cache(cache_path)


PROBE_TOOL_NAME = "gitpilot_probe_echo"


async def probe_model(
    client: Any,
    provider: str,
    model: str,
    *,
    base_url: str = "",
    cache_path: Path = PROFILE_CACHE_PATH,
    persist: bool = True,
) -> ModelProfile:
    """Ask the model one cheap question to find out how it answers.

    A single trivial tool and an instruction to call it.  Three outcomes:

    * a parsed native tool call → NATIVE;
    * text following the ReAct grammar → REACT_TEXT;
    * anything else → LITE.

    A transport failure returns the REACT_TEXT fallback and does *not* cache:
    an unreachable endpoint says nothing about the model, and caching that would
    pin a wrong answer until someone deleted the file by hand.
    """
    from .providers.base import ToolDef

    probe_tool = ToolDef(
        name=PROBE_TOOL_NAME,
        description="Echo the word given to you. Used to detect tool-calling support.",
        parameters={
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
        },
    )
    messages = [{
        "role": "user",
        "content": (
            "Call the tool named "
            f"{PROBE_TOOL_NAME} with word=\"ping\". If you cannot call tools, reply "
            "exactly:\nThought: I will echo\n"
            f"Action: {PROBE_TOOL_NAME}\nAction Input: {{\"word\": \"ping\"}}"
        ),
    }]

    try:
        response = await client.complete(
            messages, tools=[probe_tool], temperature=0, max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001 - any transport problem
        logger.debug("model probe failed for %s (%s); assuming a text grammar", model, exc)
        profile = base_profile(provider, model, Dialect.REACT_TEXT, "fallback")
        return replace(profile, notes=f"probe failed: {exc}")

    dialect = classify_probe_response(response)
    profile = base_profile(provider, model, dialect, "probe")

    if persist:
        write_probe_cache(
            _cache_key(base_url, model),
            {"dialect": dialect.value, "model": _normalise_model(model)},
            cache_path,
        )
    return profile


def classify_probe_response(response: Any) -> Dialect:
    """Read a probe answer.  Split out so it can be tested without a client."""
    if getattr(response, "tool_calls", None):
        return Dialect.NATIVE

    text = str(getattr(response, "text", "") or "")
    lowered = text.lower()
    if "action:" in lowered and PROBE_TOOL_NAME.lower() in lowered:
        return Dialect.REACT_TEXT
    return Dialect.LITE


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------


def active_model(settings: Any) -> Tuple[str, str, str]:
    """(provider, model, base_url) for the current configuration."""
    from ..settings import LLMProvider

    provider = str(getattr(settings, "provider", "") or "")
    provider = provider.split(".")[-1].lower()

    try:
        endpoint_provider = settings.provider
    except AttributeError:      # pragma: no cover - defensive
        endpoint_provider = None

    if endpoint_provider == LLMProvider.claude:
        return "claude", settings.claude.model, getattr(settings.claude, "base_url", "") or ""
    if endpoint_provider == LLMProvider.watsonx:
        return "watsonx", settings.watsonx.model_id, getattr(settings.watsonx, "url", "") or ""

    from ..direct_chat import resolve_endpoint

    endpoint = resolve_endpoint(settings)
    if endpoint is not None:
        return endpoint.provider, endpoint.model, endpoint.base_url
    return provider, "", ""


def profile_for_settings(
    settings: Any = None,
    *,
    model: Optional[str] = None,
    workspace: Optional[Path] = None,
    cache_path: Path = PROFILE_CACHE_PATH,
) -> ModelProfile:
    """The profile for the active configuration, honouring a model override."""
    from ..settings import get_settings

    settings = settings or get_settings()
    provider, configured_model, base_url = active_model(settings)
    return resolve_profile(
        provider,
        model or configured_model,
        workspace=workspace,
        base_url=base_url,
        cache_path=cache_path,
    )


def routed_model(request: str, *, category: str = "", settings: Any = None) -> Optional[str]:
    """The model :mod:`gitpilot.smart_model_router` would pick, if it can.

    Its selection has been computed and thrown away since it shipped —
    ``dispatch_request`` logs the choice and then builds an LLM from settings
    regardless.  Returning it here is what lets the loop actually use it: the
    caller passes the result to ``build_provider(model=...)``.

    ``None`` means "no opinion", which is the honest answer when the router has
    no model map for the active provider — inventing a model id the endpoint has
    never heard of would turn a working run into a 404.
    """
    try:
        from ..smart_model_router import ModelRouter

        selection = ModelRouter().select(request, category=category or None)
    except Exception as exc:  # noqa: BLE001 - routing is an optimisation
        logger.debug("model router unavailable: %s", exc)
        return None

    chosen = str(getattr(selection, "model", "") or "")
    return chosen or None
