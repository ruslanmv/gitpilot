"""Context-window usage meter — read-only snapshot for the chat UI.

Computes the active LLM's context-window utilisation: provider, model,
token budget, what's currently occupying that budget, and a short
human-readable description of the agent topology in use.

Token counting is best-effort.  When :mod:`tiktoken` is available we use
it (cl100k_base — accurate for OpenAI/Anthropic).  For local providers
without a published tokenizer we fall back to a ``len(text) // 4``
heuristic; callers can recognise that case via ``is_estimate=True`` and
the UI prefixes the numbers with ``≈`` to flag the imprecision.

Pure, side-effect-free, no I/O beyond reading settings — safe to call
from a hot endpoint on every popover open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional

from .context_budget import _TIKTOKEN, estimate_tokens
from .settings import AppSettings, LLMProvider

FLAG_CONTEXT_METER = "context_meter"

# ----------------------------------------------------------------------
# Context-window catalogue
# ----------------------------------------------------------------------
# Conservative values — when in doubt round DOWN.  We'd rather show a
# user "94% full" against a 7 800-token estimate than claim "47% full"
# against a 16 000 number the provider won't actually honour.

_DEFAULT_CONTEXT_WINDOW = 8_192

_OPENAI_WINDOWS: Mapping[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
}

_CLAUDE_WINDOWS: Mapping[str, int] = {
    "claude-opus-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
}

_WATSONX_WINDOWS: Mapping[str, int] = {
    "meta-llama/llama-3-3-70b-instruct": 131_072,
    "meta-llama/llama-3-1-70b-instruct": 131_072,
    "meta-llama/llama-3-1-8b-instruct": 131_072,
    "ibm/granite-3-8b-instruct": 4_096,
}

# Ollama / OllaBridge — keyed on the *family* prefix.  Anything not
# matched falls back to the conservative 8 k default.  These are the
# advertised values; users running with a smaller ``num_ctx`` will see
# the bar fill faster than expected, which is the safe direction.
_OLLAMA_FAMILY_WINDOWS: Mapping[str, int] = {
    "llama3": 8_192,
    "llama3.1": 131_072,
    "llama3.2": 131_072,
    "llama2": 4_096,
    "qwen2.5": 32_768,
    "qwen2": 32_768,
    "mistral": 32_768,
    "mixtral": 32_768,
    "phi3": 4_096,
    "phi": 2_048,
    "gemma2": 8_192,
    "gemma": 8_192,
    "codellama": 16_384,
    "deepseek-coder": 16_384,
}


def _ollama_window(model: str) -> int:
    """Look up the context window for an Ollama model tag (e.g. ``llama3:8b``)."""
    family = model.split(":", 1)[0].lower()
    if family in _OLLAMA_FAMILY_WINDOWS:
        return _OLLAMA_FAMILY_WINDOWS[family]
    # Try a prefix match for variants like "llama3.1:8b-instruct".
    for prefix, window in _OLLAMA_FAMILY_WINDOWS.items():
        if family.startswith(prefix):
            return window
    return _DEFAULT_CONTEXT_WINDOW


# ----------------------------------------------------------------------
# Public dataclass
# ----------------------------------------------------------------------

@dataclass
class ContextUsage:
    """Snapshot of the active model's context-window utilisation."""

    provider: str
    model: str
    context_window: int
    used: int
    reserved_response: int
    topology: str
    tool_count: int
    breakdown: Dict[str, int] = field(default_factory=dict)
    is_estimate: bool = False
    """True when token counts come from the chars/4 heuristic rather than
    a real tokenizer.  The UI prefixes such numbers with ``≈``."""

    @property
    def free(self) -> int:
        return max(0, self.context_window - self.used - self.reserved_response)

    @property
    def percent_used(self) -> float:
        if self.context_window <= 0:
            return 0.0
        return round(100.0 * self.used / self.context_window, 1)

    def to_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "context_window": self.context_window,
            "used": self.used,
            "reserved_response": self.reserved_response,
            "free": self.free,
            "percent_used": self.percent_used,
            "topology": self.topology,
            "tool_count": self.tool_count,
            "breakdown": dict(self.breakdown),
            "is_estimate": self.is_estimate,
        }


# ----------------------------------------------------------------------
# Resolvers
# ----------------------------------------------------------------------

def resolve_provider_model(settings: AppSettings) -> tuple[str, str]:
    """Return ``(provider_display_name, model_id)`` for the active config."""
    p = settings.provider
    if p == LLMProvider.openai:
        return ("OpenAI", settings.openai.model or "gpt-4o-mini")
    if p == LLMProvider.claude:
        return ("Anthropic", settings.claude.model or "claude-sonnet-4-5")
    if p == LLMProvider.watsonx:
        return ("watsonx", settings.watsonx.model_id or "")
    if p == LLMProvider.ollama:
        return ("Ollama", settings.ollama.model or "llama3")
    if p == LLMProvider.ollabridge:
        return ("OllaBridge", settings.ollabridge.model or "")
    return (str(p), "")


def resolve_context_window(settings: AppSettings) -> int:
    """Return the advertised context-window size for the active model."""
    p = settings.provider
    if p == LLMProvider.openai:
        return _OPENAI_WINDOWS.get(settings.openai.model, _DEFAULT_CONTEXT_WINDOW)
    if p == LLMProvider.claude:
        return _CLAUDE_WINDOWS.get(settings.claude.model, 200_000)
    if p == LLMProvider.watsonx:
        return _WATSONX_WINDOWS.get(settings.watsonx.model_id, _DEFAULT_CONTEXT_WINDOW)
    if p == LLMProvider.ollama:
        return _ollama_window(settings.ollama.model)
    if p == LLMProvider.ollabridge:
        return _ollama_window(settings.ollabridge.model)
    return _DEFAULT_CONTEXT_WINDOW


def has_real_tokenizer(settings: AppSettings) -> bool:
    """True when token counts will come from a real tokenizer rather
    than the chars/4 heuristic.  ``cl100k_base`` is a reasonable
    approximation for OpenAI and Anthropic; local model tokenizers are
    not bundled, so Ollama/OllaBridge falls back to the estimate."""
    if _TIKTOKEN is None:
        return False
    return settings.provider in (LLMProvider.openai, LLMProvider.claude)


# ----------------------------------------------------------------------
# Topology string
# ----------------------------------------------------------------------

def describe_topology(
    *,
    lite_mode: bool,
    tool_count: int,
    extra_tools: int = 0,
) -> str:
    """Build the one-line topology description shown in the popover.

    ``extra_tools`` covers MCP / plugin tools registered at runtime — the
    caller passes it in so this module stays import-free of those
    optional subsystems.
    """
    total_tools = tool_count + extra_tools
    if lite_mode:
        return "lite · prompt-only · 0 tools · no repo I/O"
    return f"single-agent · CrewAI ReAct · {total_tools} tools"


# ----------------------------------------------------------------------
# Token-count helpers
# ----------------------------------------------------------------------

def count_tokens(text: str) -> int:
    """Thin wrapper around :func:`context_budget.estimate_tokens` so
    callers don't have to know about the fallback hierarchy."""
    return estimate_tokens(text)


def sum_tokens(texts: Iterable[str]) -> int:
    return sum(count_tokens(t) for t in texts if t)


# ----------------------------------------------------------------------
# Real breakdown sources
# ----------------------------------------------------------------------

# Snapshot of the planner / executor / explorer persona strings that go
# into every LLM call.  We pin them here as constants (rather than
# importing from ``agentic``) so this module stays import-light and the
# token math is deterministic in tests.  When those personae change in
# ``agentic.py``, update these strings.
_PLANNER_BACKSTORY = (
    "You are an experienced staff engineer who creates plans based on FACTS, not assumptions. "
    "You have received a complete exploration report of the repository. "
    "You ONLY create plans for files that actually exist in the exploration report. "
    "You are extremely careful with DELETE actions - you verify the file exists "
    "and that it's not on the 'keep' list before marking it for deletion. "
    "When users ask to delete files, you delete individual FILES, not directory names. "
    "When users ask to ANALYZE files and GENERATE new content (code, docs, examples), "
    "you create plans that READ existing files and CREATE new files with generated content. "
    "You understand that 'analyze X and create Y' means: use tools to read X, then plan to CREATE Y. "
    "You never make changes yourself, only create detailed plans."
)

_PLANNER_ROLE = "Repository Refactor Planner"
_PLANNER_GOAL = (
    "Design safe, step-by-step refactor plans based on ACTUAL repository state "
    "discovered during exploration"
)

_EXPLORER_ROLE = "Repository Explorer"
_EXPLORER_GOAL = (
    "Thoroughly explore the repository structure, identify key files, and report findings"
)
_EXPLORER_BACKSTORY = (
    "You are a meticulous code archaeologist. You use the available tools to "
    "list files, read content, and build a complete picture of the repository "
    "before any change is planned."
)

_LITE_ROLE = "GitPilot Lite"
_LITE_GOAL = "Help the user with their repository"
_LITE_BACKSTORY = "You are a helpful coding assistant. Be concise."


def system_prompt_text(*, lite_mode: bool) -> str:
    """Return the persona text that the active topology will inject into
    every LLM call.  Used for the ``system_prompt`` breakdown row."""
    if lite_mode:
        return " ".join((_LITE_ROLE, _LITE_GOAL, _LITE_BACKSTORY))
    return " ".join(
        (
            _EXPLORER_ROLE,
            _EXPLORER_GOAL,
            _EXPLORER_BACKSTORY,
            _PLANNER_ROLE,
            _PLANNER_GOAL,
            _PLANNER_BACKSTORY,
        )
    )


def count_system_prompt_tokens(*, lite_mode: bool) -> int:
    return count_tokens(system_prompt_text(lite_mode=lite_mode))


def count_messages_tokens(messages: Iterable[object]) -> int:
    """Sum estimated tokens over an iterable of message-like objects.

    Accepts any object exposing a ``.content`` attribute (matches the
    :class:`gitpilot.session.Message` dataclass) or a ``"content"``
    mapping key.  Other shapes are ignored, which is the safe default
    for partially-typed history records.
    """
    total = 0
    for m in messages:
        if m is None:
            continue
        if isinstance(m, dict):
            content = m.get("content")
        else:
            content = getattr(m, "content", None)
        if isinstance(content, str) and content:
            total += count_tokens(content)
    return total


def count_tool_schema_tokens(tool_lists: Iterable[Iterable[object]]) -> int:
    """Sum tokens over every tool's ``name`` + ``description`` + JSON
    schema across the supplied tool lists.  This approximates what the
    LLM sees in its function/tool-calling preamble.

    Tools that don't expose name/description are skipped silently —
    we're not the place to enforce CrewAI tool contracts.
    """
    import json as _json

    total = 0
    for group in tool_lists:
        if not group:
            continue
        for tool in group:
            name = getattr(tool, "name", "") or ""
            description = getattr(tool, "description", "") or ""
            schema = getattr(tool, "args_schema", None)
            schema_text = ""
            if schema is not None:
                # Pydantic v2 model class — model_json_schema() is cheap.
                model_schema = getattr(schema, "model_json_schema", None)
                if callable(model_schema):
                    try:
                        schema_text = _json.dumps(model_schema())
                    except Exception:  # pragma: no cover - defensive
                        schema_text = ""
                else:
                    schema_text = str(schema)
            total += count_tokens(f"{name} {description} {schema_text}")
    return total


# ----------------------------------------------------------------------
# Top-level builder
# ----------------------------------------------------------------------

# Reserved-for-response budget: the LLM needs headroom to actually emit
# an answer.  4 k is a sane fixed value across providers — small enough
# not to crowd Ollama's 8 k window, large enough for a reasonable plan.
RESERVED_RESPONSE_TOKENS = 4_096


def build_usage(
    settings: AppSettings,
    *,
    breakdown: Mapping[str, int],
    tool_count: int,
    lite_mode: bool,
    extra_tools: int = 0,
    reserved_response: Optional[int] = None,
) -> ContextUsage:
    """Assemble a :class:`ContextUsage` from the inputs the API endpoint
    can cheaply collect.  All token counts come from the caller — this
    function only does arithmetic and lookup, so it's trivially testable."""
    provider, model = resolve_provider_model(settings)
    window = resolve_context_window(settings)
    reserved = RESERVED_RESPONSE_TOKENS if reserved_response is None else reserved_response
    used = sum(int(v) for v in breakdown.values() if v)
    topology = describe_topology(
        lite_mode=lite_mode, tool_count=tool_count, extra_tools=extra_tools
    )
    return ContextUsage(
        provider=provider,
        model=model,
        context_window=window,
        used=used,
        reserved_response=reserved,
        topology=topology,
        tool_count=tool_count + extra_tools,
        breakdown=dict(breakdown),
        is_estimate=not has_real_tokenizer(settings),
    )
