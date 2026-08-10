# gitpilot/reasoning_normalizer.py
"""
Reasoning Model Normalizer — makes CrewAI compatible with DeepSeek-R1,
QwQ, Marco-O1, and other reasoning models.

Why this module exists:
─────────────────────────────────────────────────────────────────
Reasoning models (deepseek-r1, qwq, marco-o1) produce output like:

    <think>
    Let me think about this step by step...
    I need to call Get repository summary first.
    </think>
    Thought: I need to call Get repository summary
    Action: Get repository summary
    Action Input: {}

CrewAI's ReAct parser expects output starting with `Thought:` and
cannot handle the `<think>...</think>` wrapper. When a reasoning model
returns such output, CrewAI's parser fails silently and returns None,
causing:

    ValueError: Invalid response from LLM call - None or empty.

This affects ALL deepseek-r1 sizes (1.5b → 70b). It's not a model
quality issue — it's a format incompatibility.

Industry-standard solution:
─────────────────────────────────────────────────────────────────
Strip the reasoning content (between <think> and </think> tags)
BEFORE passing the response to CrewAI's ReAct parser. This is what
vLLM's DeepSeekR1ReasoningParser and Ollama's `think` parameter do
internally.

References:
- vLLM reasoning parser: https://docs.vllm.ai/en/v0.9.0/api/vllm/reasoning/deepseek_r1_reasoning_parser.html
- DeepSeek official docs: https://api-docs.deepseek.com/guides/reasoning_model
- Ollama thinking capability: https://docs.ollama.com/capabilities/thinking
- Aider's workaround: https://github.com/Aider-AI/aider/issues/3008

Our approach:
─────────────────────────────────────────────────────────────────
1. `strip_reasoning_content(text)` — pure function that handles all
   edge cases: nested tags, orphaned tags, partial tags, no tags.

2. `ReasoningAwareLLM` — transparent wrapper around crewai.LLM that
   intercepts responses and strips reasoning before CrewAI parses.

3. `wrap_if_reasoning_model(llm, settings)` — factory that returns
   either the wrapped or the original LLM depending on the model
   name. Zero overhead for non-reasoning models.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Pattern matching — reasoning models that emit <think> blocks
# ─────────────────────────────────────────────────────────────────

# Model name patterns that indicate a reasoning model.
# Uses substring matching so "deepseek-r1" catches all size variants
# (deepseek-r1:1.5b, deepseek-r1:14b, deepseek-r1:70b, deepseek-r1:latest).
REASONING_MODEL_PATTERNS = (
    "deepseek-r1",
    "deepseek-reasoner",
    "qwq",              # Qwen reasoning models (qwq-32b, qwq-preview)
    "marco-o1",         # Alibaba reasoning model
    "o1-",              # OpenAI o1-series via Ollama proxies
    "r1-distill",       # DeepSeek R1 distilled variants
    "openthinker",      # Open-source reasoning model
)


def is_reasoning_model(model_name: str) -> bool:
    """Return True if the given model name matches a known reasoning model."""
    if not model_name:
        return False
    m = str(model_name).lower().strip()
    # Strip provider prefixes: "ollama/deepseek-r1:14b" -> "deepseek-r1:14b"
    if "/" in m:
        m = m.split("/", 1)[1]
    return any(pattern in m for pattern in REASONING_MODEL_PATTERNS)


# ─────────────────────────────────────────────────────────────────
# Core: strip reasoning content from model output
# ─────────────────────────────────────────────────────────────────

# Matches <think>...</think> blocks including multiline content
_THINK_BLOCK_RE = re.compile(
    r"<think\b[^>]*>.*?</think\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Matches orphaned <think> at the start (no closing tag — truncated output)
_ORPHAN_THINK_OPEN_RE = re.compile(
    r"^\s*<think\b[^>]*>.*?(?=Thought:|Action:|Final Answer:|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Matches orphaned closing </think> (appears without opening)
_ORPHAN_THINK_CLOSE_RE = re.compile(
    r"</think\s*>",
    re.IGNORECASE,
)

# Alternative tag styles some models use
_ALT_THINK_PATTERNS = [
    # <thought>...</thought>
    (re.compile(r"<thought\b[^>]*>.*?</thought\s*>", re.DOTALL | re.IGNORECASE), ""),
    # <reasoning>...</reasoning>
    (re.compile(r"<reasoning\b[^>]*>.*?</reasoning\s*>", re.DOTALL | re.IGNORECASE), ""),
]


def strip_reasoning_content(text: str) -> tuple[str, str]:
    """Strip reasoning content from model output.

    Handles all edge cases:
    - Standard: <think>...</think> followed by answer
    - Nested: <think> with inner <think> tags (iterative removal)
    - Orphan open: <think>... (no closing tag — truncated)
    - Orphan close: ...</think> (appears without opening)
    - Alternative tags: <thought>, <reasoning>
    - No tags: pass through unchanged

    Args:
        text: Raw LLM output that may contain reasoning blocks

    Returns:
        (cleaned_text, reasoning_content) — cleaned_text is safe for
        CrewAI's ReAct parser, reasoning_content contains what was
        stripped (for logging/debugging).
    """
    if not text or not isinstance(text, str):
        return text or "", ""

    original = text
    reasoning_parts: List[str] = []

    # 1. Extract and remove all well-formed <think>...</think> blocks
    def _capture(match: re.Match) -> str:
        reasoning_parts.append(match.group(0))
        return ""

    # Iteratively remove until no more matches (handles nested tags)
    prev = None
    while prev != text:
        prev = text
        text = _THINK_BLOCK_RE.sub(_capture, text)

    # 2. Handle orphan opening <think> (no closing tag — common with streaming cutoffs)
    orphan_match = _ORPHAN_THINK_OPEN_RE.match(text)
    if orphan_match:
        reasoning_parts.append(orphan_match.group(0))
        text = text[orphan_match.end():]

    # 3. Remove any remaining orphan </think> tags
    text = _ORPHAN_THINK_CLOSE_RE.sub("", text)

    # 4. Handle alternative tag styles
    for pattern, replacement in _ALT_THINK_PATTERNS:
        def _capture_alt(match: re.Match) -> str:
            reasoning_parts.append(match.group(0))
            return replacement
        text = pattern.sub(_capture_alt, text)

    # 5. Clean up: strip leading/trailing whitespace, collapse triple+ newlines
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    reasoning = "\n".join(reasoning_parts).strip()

    if reasoning and logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "[ReasoningNormalizer] Stripped %d chars of reasoning from %d-char response",
            len(reasoning), len(original),
        )

    return text, reasoning


# ─────────────────────────────────────────────────────────────────
# ReasoningAwareLLM — transparent wrapper around crewai.LLM
# ─────────────────────────────────────────────────────────────────


class ReasoningAwareLLM:
    """Drop-in wrapper around crewai.LLM that normalizes reasoning model output.

    Intercepts every response from the wrapped LLM and strips <think>...</think>
    blocks before returning to CrewAI's ReAct parser. The wrapper is fully
    transparent — all other attributes and methods delegate to the inner LLM.

    Usage:
        from crewai import LLM
        from gitpilot.reasoning_normalizer import ReasoningAwareLLM

        base_llm = LLM(model="ollama/deepseek-r1:14b", ...)
        wrapped = ReasoningAwareLLM(base_llm)
        # Pass `wrapped` to CrewAI Agents — they won't see <think> tags

    Why a wrapper instead of subclassing:
    - CrewAI's LLM class changes between versions; composition is safer
    - Works with any LLM-like object that has a `.call()` or `.__call__()` method
    - Zero risk of breaking CrewAI internals
    """

    def __init__(self, inner_llm: Any) -> None:
        """Wrap an existing CrewAI LLM instance.

        Args:
            inner_llm: Any object with a .call() method (typically crewai.LLM)
        """
        object.__setattr__(self, "_inner", inner_llm)
        object.__setattr__(self, "_last_reasoning", "")
        object.__setattr__(self, "_strip_count", 0)

    # ── Core method: intercept .call() ────────────────────────────

    def call(
        self,
        messages: Any,
        tools: Optional[List[Any]] = None,
        callbacks: Optional[List[Any]] = None,
        available_functions: Optional[dict] = None,
        from_task: Optional[Any] = None,
        from_agent: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        """Call the wrapped LLM and normalize any reasoning content.

        Forwards all arguments to the inner LLM, then strips <think> blocks
        from string responses before returning. Non-string responses (dicts,
        tool calls) are passed through unchanged.
        """
        # Also clean up historical messages — DeepSeek's own docs recommend
        # removing <think>{cot}</think> from prior messages to avoid context
        # length explosion and performance degradation.
        cleaned_messages = self._clean_messages(messages)

        try:
            result = self._inner.call(
                cleaned_messages,
                tools=tools,
                callbacks=callbacks,
                available_functions=available_functions,
                from_task=from_task,
                from_agent=from_agent,
                **kwargs,
            )
        except TypeError:
            # Older CrewAI versions don't accept from_task/from_agent
            try:
                result = self._inner.call(
                    cleaned_messages,
                    tools=tools,
                    callbacks=callbacks,
                    available_functions=available_functions,
                )
            except TypeError:
                # Minimal signature fallback
                result = self._inner.call(cleaned_messages)

        return self._normalize_response(result)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Support callable-style invocation (some CrewAI code paths use this)."""
        return self.call(*args, **kwargs)

    # ── Response normalization ────────────────────────────────────

    def _normalize_response(self, response: Any) -> Any:
        """Strip reasoning content from the LLM response.

        Handles multiple response shapes that CrewAI might return:
        - Plain string (most common)
        - Dict with 'content' key
        - Dict with 'choices' → OpenAI-compatible shape
        - Objects with .content attribute
        """
        # Plain string
        if isinstance(response, str):
            cleaned, reasoning = strip_reasoning_content(response)
            if reasoning:
                object.__setattr__(self, "_last_reasoning", reasoning)
                object.__setattr__(self, "_strip_count", self._strip_count + 1)
                logger.info(
                    "[ReasoningNormalizer] Stripped reasoning block from LLM response "
                    "(%d chars removed, %d chars remaining)",
                    len(reasoning), len(cleaned),
                )
            # If the cleaned result is empty, return a safe fallback so CrewAI
            # doesn't crash with "Invalid response from LLM call - None or empty"
            if not cleaned.strip():
                logger.warning(
                    "[ReasoningNormalizer] Response was empty after stripping reasoning. "
                    "This usually means the model only produced <think> content without "
                    "a real answer. Returning fallback text."
                )
                return (
                    "Thought: I need to gather more information before I can answer.\n"
                    "Final Answer: I apologize, but I was unable to produce a clear "
                    "response. Please try again or switch to a non-reasoning model."
                )
            return cleaned

        # Dict response (openai-compatible shape)
        if isinstance(response, dict):
            # OpenAI format: {"choices": [{"message": {"content": "..."}}]}
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                for choice in choices:
                    msg = choice.get("message") if isinstance(choice, dict) else None
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        cleaned, reasoning = strip_reasoning_content(msg["content"])
                        msg["content"] = cleaned
                        if reasoning:
                            # Store in reasoning_content (vLLM/DeepSeek convention)
                            msg.setdefault("reasoning_content", reasoning)
                return response
            # Simple dict with content field
            if isinstance(response.get("content"), str):
                cleaned, reasoning = strip_reasoning_content(response["content"])
                response["content"] = cleaned
                if reasoning:
                    response.setdefault("reasoning_content", reasoning)
                return response

        # Object with .content attribute
        if hasattr(response, "content") and isinstance(response.content, str):
            cleaned, reasoning = strip_reasoning_content(response.content)
            try:
                response.content = cleaned
            except AttributeError:
                pass  # read-only attribute
            return response

        # Unknown shape — pass through unchanged
        return response

    def _clean_messages(self, messages: Any) -> Any:
        """Strip <think> blocks from historical messages.

        DeepSeek's official docs state that leaving <think>{cot}</think> in
        prior messages degrades performance and explodes context length.
        """
        if not isinstance(messages, list):
            return messages

        cleaned = []
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and ("<think>" in content.lower() or "<thought>" in content.lower()):
                    new_content, _ = strip_reasoning_content(content)
                    cleaned.append({**msg, "content": new_content})
                    continue
            cleaned.append(msg)
        return cleaned

    # ── Delegate all other attribute access to the inner LLM ──────

    def __getattr__(self, name: str) -> Any:
        """Forward any attribute not defined on the wrapper to the inner LLM."""
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Forward attribute sets to the inner LLM (except our private state)."""
        if name in ("_inner", "_last_reasoning", "_strip_count"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    def __repr__(self) -> str:
        return f"ReasoningAwareLLM(inner={self._inner!r}, strip_count={self._strip_count})"


# ─────────────────────────────────────────────────────────────────
# Factory: wrap only if needed
# ─────────────────────────────────────────────────────────────────


#: Built once, on first use. Defining it at import time would drag CrewAI —
#: and LiteLLM, and ~180 packages — into every process that imports this
#: module, which is the startup cost llm_provider goes out of its way to avoid.
_CREWAI_WRAPPER_CLASS: Any = None


def _crewai_wrapper_class() -> Any:
    """A ReasoningAwareLLM that CrewAI's ``Agent`` will accept, or None.

    Composition was the original choice here, and the docstring above still
    explains why: CrewAI's LLM class changes between versions, so wrapping
    rather than subclassing looked like the safer bet. It stopped being safe
    when ``Agent.llm`` became a validated pydantic field typed ``str |
    BaseLLM``. A plain wrapper is neither, so every agent built with a
    reasoning model died at construction:

        2 validation errors for Agent
        llm.str      Input should be a valid string
        llm.BaseLLM  Input should be a valid dictionary or instance of BaseLLM

    ``BaseLLM`` is an ABC with exactly one abstract method — ``call`` — which
    is the method this wrapper existed to intercept anyway. So the subclass
    is small, and it satisfies the validator by being what it claims to be.

    Returns None when CrewAI is absent, and the plain wrapper is used: the
    direct-provider path has no CrewAI to satisfy.
    """
    global _CREWAI_WRAPPER_CLASS
    if _CREWAI_WRAPPER_CLASS is not None:
        return _CREWAI_WRAPPER_CLASS

    try:
        from crewai.llms.base_llm import BaseLLM
    except Exception:  # pragma: no cover - CrewAI not installed
        return None

    from pydantic import PrivateAttr

    class CrewAIReasoningAwareLLM(BaseLLM):  # type: ignore[misc, valid-type]
        """Strips reasoning from the inner LLM's replies, and is a BaseLLM."""

        _inner: Any = PrivateAttr(default=None)
        _last_reasoning: str = PrivateAttr(default="")
        _strip_count: int = PrivateAttr(default=0)

        def __init__(self, inner_llm: Any) -> None:
            # Carry the inner model's identity across. CrewAI reads `model`
            # for logging, token accounting and context-window sizing; an
            # empty one turns those into guesses.
            super().__init__(model=getattr(inner_llm, "model", "") or "unknown")
            self._inner = inner_llm

        def call(self, *args: Any, **kwargs: Any) -> Any:
            response = self._inner.call(*args, **kwargs)
            if not isinstance(response, str):
                return response
            cleaned, reasoning = strip_reasoning_content(response)
            if reasoning:
                self._last_reasoning = reasoning
                self._strip_count += 1
            # An answer that was *only* reasoning leaves nothing behind.
            # Handing CrewAI "" reads as a failed call; the raw text at least
            # carries the model's work.
            return cleaned if cleaned.strip() else response

        # CrewAI asks the LLM about itself before and during a run. Answer
        # from the real client rather than from BaseLLM's defaults, which
        # describe nothing in particular.
        def supports_function_calling(self) -> bool:
            inner = self._inner
            if hasattr(inner, "supports_function_calling"):
                try:
                    return bool(inner.supports_function_calling())
                except Exception:
                    pass
            return False

        def supports_stop_words(self) -> bool:
            inner = self._inner
            if hasattr(inner, "supports_stop_words"):
                try:
                    return bool(inner.supports_stop_words())
                except Exception:
                    pass
            return True

        def get_context_window_size(self) -> int:
            inner = self._inner
            if hasattr(inner, "get_context_window_size"):
                try:
                    return int(inner.get_context_window_size())
                except Exception:
                    pass
            return super().get_context_window_size()

        def __getattr__(self, name: str) -> Any:
            # Reached only when normal lookup fails, so fields and methods
            # defined here always win. Everything else — the attributes a
            # given CrewAI version happens to poke at — comes from the
            # client being wrapped.
            try:
                return super().__getattr__(name)  # type: ignore[misc]
            except AttributeError:
                pass
            inner = object.__getattribute__(self, "__pydantic_private__").get("_inner")
            if inner is None:
                raise AttributeError(name)
            return getattr(inner, name)

    _CREWAI_WRAPPER_CLASS = CrewAIReasoningAwareLLM
    return _CREWAI_WRAPPER_CLASS


def wrap_if_reasoning_model(llm: Any, model_name: str) -> Any:
    """Return a reasoning-aware wrapper if model_name is a reasoning model,
    otherwise return the original llm unchanged.

    This is the safe entry point — zero overhead for non-reasoning models.

    Args:
        llm: The underlying LLM instance (e.g., crewai.LLM)
        model_name: The model identifier (e.g., "ollama/deepseek-r1:14b")

    Returns:
        Either the wrapped LLM or the original.
    """
    if not is_reasoning_model(model_name):
        return llm

    logger.info(
        "[ReasoningNormalizer] Wrapping LLM for reasoning model: %s", model_name
    )

    crewai_class = _crewai_wrapper_class()
    if crewai_class is not None:
        try:
            return crewai_class(llm)
        except Exception as exc:  # pragma: no cover - unexpected CrewAI shape
            # Never let normalization be the reason a chat cannot happen.
            # Unstripped <think> tags are a cosmetic problem; no LLM is not.
            logger.warning(
                "[ReasoningNormalizer] Could not build the CrewAI-compatible "
                "wrapper (%s); using the model unwrapped",
                exc,
            )
            return llm

    return ReasoningAwareLLM(llm)
