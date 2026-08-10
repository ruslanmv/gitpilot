# gitpilot/resilience.py
"""Production resilience primitives: circuit breaker, timeouts, health probes.

Keeps the app responsive when upstream LLM providers are slow or down.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------
AGENT_TIMEOUT_SECONDS = int(os.getenv("GITPILOT_AGENT_TIMEOUT", "300"))  # 5 min
LLM_CONNECT_TIMEOUT = float(os.getenv("GITPILOT_LLM_CONNECT_TIMEOUT", "15"))
LLM_READ_TIMEOUT = float(os.getenv("GITPILOT_LLM_READ_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------
class CircuitState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # failing — reject immediately
    HALF_OPEN = "half_open"  # testing recovery


@dataclass
class CircuitBreaker:
    """Simple circuit breaker for upstream LLM / OllaBridge calls.

    - After ``failure_threshold`` consecutive failures → OPEN (reject fast).
    - After ``recovery_timeout`` seconds → HALF_OPEN (allow one probe).
    - If the probe succeeds → CLOSED; if it fails → OPEN again.
    """

    name: str = "llm"
    failure_threshold: int = int(os.getenv("GITPILOT_CB_FAILURES", "3"))
    recovery_timeout: float = float(os.getenv("GITPILOT_CB_RECOVERY", "30"))

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)

    def record_success(self) -> None:
        self._failure_count = 0
        if self.state != CircuitState.CLOSED:
            logger.info("[CircuitBreaker:%s] recovered → CLOSED", self.name)
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.warning(
                    "[CircuitBreaker:%s] %d failures → OPEN (reject for %ds)",
                    self.name,
                    self._failure_count,
                    self.recovery_timeout,
                )
            self.state = CircuitState.OPEN

    def allow_request(self) -> bool:
        """Return True if the request should proceed."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                logger.info("[CircuitBreaker:%s] recovery window → HALF_OPEN", self.name)
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN — allow exactly one probe
        return True

    def status_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
        }


# Global circuit breaker instance for LLM calls
llm_circuit = CircuitBreaker(name="llm_provider")


# ---------------------------------------------------------------------------
# Timeout wrapper for CrewAI kickoff calls
# ---------------------------------------------------------------------------
T = TypeVar("T")


async def run_with_timeout(
    coro,
    timeout: float = AGENT_TIMEOUT_SECONDS,
    label: str = "agent",
) -> Any:
    """Run an async operation with a hard timeout.

    Wraps ``asyncio.wait_for`` with a descriptive error message.
    Use this around ``asyncio.to_thread(ctx.run, _run)`` calls.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("[Timeout] %s exceeded %ds limit", label, timeout)
        raise TimeoutError(
            f"Agent operation '{label}' timed out after {timeout}s. "
            "The LLM provider may be overloaded or unreachable."
        ) from None


# ---------------------------------------------------------------------------
# Deep health probe
# ---------------------------------------------------------------------------
async def deep_health_check() -> dict:
    """Check LLM provider connectivity and return detailed status."""
    from .settings import get_settings, LLMProvider

    settings = get_settings()
    provider = settings.provider
    result: dict[str, Any] = {
        "status": "healthy",
        "service": "gitpilot-backend",
        "provider": provider.value,
        "circuit_breaker": llm_circuit.status_dict(),
        "crewai_loaded": False,
    }

    # Check if CrewAI is loaded
    try:
        from .agentic import _crewai_cache
        result["crewai_loaded"] = bool(_crewai_cache)
    except Exception:
        pass

    # Check LLM provider connectivity
    try:
        base_url = _get_provider_base_url(settings, provider)
        if base_url:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
            ) as client:
                if provider == LLMProvider.ollabridge:
                    resp = await client.get(f"{base_url.rstrip('/')}/health")
                else:
                    resp = await client.get(f"{base_url.rstrip('/')}/v1/models")
                result["provider_reachable"] = resp.status_code < 500
                result["provider_status_code"] = resp.status_code
        else:
            result["provider_reachable"] = None
    except httpx.ConnectError:
        result["status"] = "degraded"
        result["provider_reachable"] = False
        result["provider_error"] = "connection_refused"
    except httpx.TimeoutException:
        result["status"] = "degraded"
        result["provider_reachable"] = False
        result["provider_error"] = "timeout"
    except Exception as exc:
        result["status"] = "degraded"
        result["provider_reachable"] = False
        result["provider_error"] = str(exc)

    # Memory info
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result["memory_mb"] = round(usage.ru_maxrss / 1024, 1)  # Linux: KB → MB
    except Exception:
        pass

    return result


def _get_provider_base_url(settings, provider) -> str | None:
    """Extract the base URL for the active provider."""
    from .settings import LLMProvider

    if provider == LLMProvider.ollabridge:
        from .settings import OLLABRIDGE_DEFAULT_BASE_URL

        return settings.ollabridge.base_url or os.getenv(
            "OLLABRIDGE_BASE_URL", OLLABRIDGE_DEFAULT_BASE_URL
        )
    if provider == LLMProvider.ollama:
        return settings.ollama.base_url or os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
    if provider == LLMProvider.openai:
        return settings.openai.base_url or "https://api.openai.com"
    if provider == LLMProvider.claude:
        return settings.claude.base_url or "https://api.anthropic.com"
    if provider == LLMProvider.watsonx:
        return settings.watsonx.base_url or "https://us-south.ml.cloud.ibm.com"
    return None
