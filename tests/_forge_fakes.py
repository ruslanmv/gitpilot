"""In-process fakes of the GitHub and GitLab HTTP APIs for tests.

Every outgoing ``httpx.AsyncClient`` request is routed to a handler registered
by (method, host, path regex), and recorded, so a test can assert both what
GitPilot did and — for provider isolation — which host saw which credential.
No network access and no real credentials are involved.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

Handler = Callable[[httpx.Request, "re.Match[str]"], httpx.Response]


@dataclass
class Recorded:
    method: str
    host: str
    path: str
    params: Dict[str, str]
    headers: Dict[str, str]
    body: Any

    @property
    def bearer(self) -> Optional[str]:
        value = self.headers.get("authorization", "")
        return value[7:] if value.lower().startswith("bearer ") else None


@dataclass
class FakeForges:
    routes: List[Tuple[str, str, "re.Pattern[str]", Handler]] = field(default_factory=list)
    calls: List[Recorded] = field(default_factory=list)

    def on(self, method: str, host: str, path_regex: str, handler: Any) -> "FakeForges":
        """Register a handler; ``handler`` may be a callable, a dict/list (JSON 200) or an httpx.Response."""
        if not callable(handler):
            payload = handler

            def handler(_req, _m, payload=payload):  # noqa: E306
                if isinstance(payload, httpx.Response):
                    return payload
                return httpx.Response(200, json=payload)
        self.routes.append((method.upper(), host, re.compile(f"^{path_regex}$"), handler))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            body: Any = json.loads(request.content) if request.content else None
        except ValueError:
            body = request.content.decode("utf-8", errors="replace")
        rec = Recorded(
            method=request.method, host=request.url.host, path=request.url.path,
            params=dict(request.url.params), headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
        )
        self.calls.append(rec)
        # The raw (still-encoded) path keeps %2F in GitLab project paths visible.
        raw_path = request.url.raw_path.decode("ascii").split("?", 1)[0]
        for method, host, pattern, handler in self.routes:
            if method == request.method and host == request.url.host:
                match = pattern.match(raw_path)
                if match:
                    return handler(request, match)
        return httpx.Response(404, json={"message": f"no fake for {request.method} {request.url.host}{raw_path}"})

    def to(self, host: str) -> List[Recorded]:
        return [c for c in self.calls if c.host == host]

    def install(self, monkeypatch) -> "FakeForges":
        real = httpx.AsyncClient
        transport = httpx.MockTransport(self)

        # A subclass, not a factory function: libraries imported later (openai,
        # litellm) subclass ``httpx.AsyncClient`` themselves.
        class _FakeAsyncClient(real):  # type: ignore[misc, valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        return self


GITLAB = "gitlab.example.com"
GITHUB_API = "api.github.com"
GITHUB_WEB = "github.com"


def gitlab_project(pid: int = 113, path: str = "enterprise/team/repo", *, level: int = 30,
                   visibility: str = "private", default_branch: str = "main") -> Dict[str, Any]:
    """A GitLab project payload as /projects returns it (non-simple)."""
    return {
        "id": pid, "name": path.rsplit("/", 1)[-1].title(), "path": path.rsplit("/", 1)[-1],
        "path_with_namespace": path, "namespace": {"full_path": path.rsplit("/", 1)[0]},
        "default_branch": default_branch, "visibility": visibility,
        "web_url": f"https://{GITLAB}/{path}",
        "permissions": {"project_access": {"access_level": level}, "group_access": None},
    }
