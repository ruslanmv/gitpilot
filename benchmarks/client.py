"""A dependency-free OpenAI-compatible chat client with honest error accounting.

Infrastructure failures (timeouts, connection errors, 429/5xx) are reported as
such and retried with backoff; a wrong answer is never retried. That split is
what keeps an overloaded gateway from showing up in the report as a bad model.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: HTTP statuses worth retrying: the request may succeed on a second try.
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove ``<think>`` blocks so reasoning models are graded on their answer.

    An unterminated ``<think>`` (the model ran out of tokens mid-thought) leaves
    no answer at all, which is the honest reading.
    """
    text = _THINK_BLOCK.sub("", text or "")
    text = _THINK_OPEN.sub("", text)
    return text.strip()


@dataclass
class ChatResult:
    """One completed (or failed) request."""

    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    latency_s: float = 0.0
    served_model: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    #: None on success; otherwise "timeout", "connection", "http_<status>" or "bad_response".
    error_kind: Optional[str] = None
    error_detail: str = ""
    attempts: int = 1
    finish_reason: str = ""
    #: The server's Retry-After, in seconds, when it sent one.
    retry_after_s: Optional[float] = None

    @property
    def is_rate_limited(self) -> bool:
        return self.error_kind == "http_429"

    @property
    def ok(self) -> bool:
        return self.error_kind is None

    @property
    def is_infra_error(self) -> bool:
        """A failure of the serving path rather than of the model.

        Timeouts, connection errors, 429/5xx, credentials (401/403) and "model not
        found" (OllaBridge's catalog can list a model the upstream no longer
        serves) say nothing about the model's quality. A refusal of the request's
        *capability* — "does not support tools" — does, and counts against it.
        """
        if self.error_kind is None:
            return False
        if not self.error_kind.startswith("http_"):
            return True
        try:
            status = int(self.error_kind[5:])
        except ValueError:
            return True
        detail = self.error_detail.lower()
        if "tools_unsupported" in detail or "does not support tools" in detail:
            return False
        if status in RETRYABLE_STATUS or status in (401, 403, 404):
            return True
        return "not found" in detail or "unsupported_capability" in detail

    @property
    def is_retryable(self) -> bool:
        """Worth a second attempt: transient, not a permanent refusal."""
        if self.error_kind in ("timeout", "connection"):
            return True
        if self.error_kind and self.error_kind.startswith("http_"):
            try:
                return int(self.error_kind[5:]) in RETRYABLE_STATUS
            except ValueError:
                return False
        return False

    def output_tokens(self) -> tuple[int, bool]:
        """Completion tokens, and whether the count is an estimate (~4 chars/token)."""
        if self.completion_tokens:
            return self.completion_tokens, False
        raw = self.text + json.dumps(self.tool_calls) if self.tool_calls else self.text
        return max(1, round(len(raw) / 4)), True


class ChatClient:
    """POST /chat/completions against one base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_s: float = 180.0,
        retries: int = 1,
        backoff_s: float = 3.0,
        rate_limit_retries: int = 4,
        max_wait_s: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.retries = max(0, retries)
        self.backoff_s = backoff_s
        #: A 429 is the server asking us to slow down, not a failure: it gets its
        #: own, larger retry budget and honours Retry-After.
        self.rate_limit_retries = max(0, rate_limit_retries)
        self.max_wait_s = max_wait_s
        self.sleep = time.sleep  # injectable for tests

    @property
    def url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        seed: Optional[int] = 7,
    ) -> ChatResult:
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": 1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if seed is not None:
            body["seed"] = seed
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        payload = json.dumps(body).encode("utf-8")

        result = ChatResult()
        attempt = failures = throttles = 0
        while True:
            attempt += 1
            result = self._once(payload)
            result.attempts = attempt
            if result.ok or not result.is_retryable:
                return result
            if result.error_kind == "http_429":
                throttles += 1
                if throttles > self.rate_limit_retries:
                    return result
                wait = max(result.retry_after_s or 0.0, self.backoff_s * (2 ** throttles))
            else:
                failures += 1
                if failures > self.retries:
                    return result
                wait = self.backoff_s * (3 ** (failures - 1))
            self.sleep(min(wait, self.max_wait_s))

    def _once(self, payload: bytes) -> ChatResult:
        request = urllib.request.Request(self.url, data=payload, headers=self._headers(), method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            retry_after = None
            try:
                retry_after = float(exc.headers.get("Retry-After", "")) if exc.headers else None
            except (TypeError, ValueError):
                retry_after = None
            return ChatResult(latency_s=time.monotonic() - started,
                              error_kind=f"http_{exc.code}", error_detail=detail,
                              retry_after_s=retry_after)
        except TimeoutError:
            return ChatResult(latency_s=time.monotonic() - started, error_kind="timeout",
                              error_detail=f"no answer within {self.timeout_s:.0f}s")
        except (urllib.error.URLError, OSError) as exc:
            kind = "timeout" if "timed out" in str(exc).lower() else "connection"
            return ChatResult(latency_s=time.monotonic() - started, error_kind=kind,
                              error_detail=str(exc)[:400])
        latency = time.monotonic() - started
        return parse_completion(raw, latency)


def parse_completion(raw: bytes | str, latency_s: float = 0.0) -> ChatResult:
    """Read an OpenAI-shaped response defensively."""
    try:
        data = json.loads(raw)
        choice = (data.get("choices") or [])[0]
    except (ValueError, IndexError, AttributeError, TypeError):
        return ChatResult(latency_s=latency_s, error_kind="bad_response",
                          error_detail=str(raw)[:400])

    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    calls = []
    for call in message.get("tool_calls") or []:
        function = (call or {}).get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                arguments = {"__unparsed__": arguments}
        calls.append({"name": function.get("name") or "", "arguments": arguments or {}})

    usage = data.get("usage") or {}
    return ChatResult(
        text=strip_reasoning(str(content or "")),
        tool_calls=calls,
        latency_s=latency_s,
        served_model=str(data.get("model") or ""),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        finish_reason=str(choice.get("finish_reason") or ""),
    )
