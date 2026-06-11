# Sandbox provider contract

GitPilot validates generated patches in a pluggable sandbox. Providers
implement the `SandboxProvider` ABC
(`gitpilot.sandbox_providers.base.SandboxProvider`).

> The provider package is `gitpilot.sandbox_providers` (not `gitpilot.sandbox`)
> because a `gitpilot/sandbox.py` module already exists for an unrelated
> local-sandbox feature.

## ABC

```python
class SandboxProvider(ABC):
    name: str

    def health(self) -> bool: ...
    def run(self, **payload) -> SandboxResult: ...
    def validate_patch(self, **payload) -> SandboxResult: ...
```

`health()` must **degrade gracefully** — return `False` when the sandbox is
unreachable, never raise.

## MatrixLab client

`gitpilot.sandbox_providers.matrixlab_client.MatrixLabClient` implements the
ABC against MatrixLab. Reads `MATRIXLAB_URL` (default `http://localhost:8765`)
and `MATRIXLAB_TOKEN` (sent as `Authorization: Bearer ...`).

### Endpoints

* `GET  {MATRIXLAB_URL}/health`
* `POST {MATRIXLAB_URL}/repo/run`
* `POST {MATRIXLAB_URL}/repo/validate-patch`

### Request body

```jsonc
{
  "client_id": "...",
  "workspace_id": "...",
  "repo_url": "...",
  "branch": "...",
  "profile": "default",
  "commands": ["pytest -q"],   // optional
  "timeout_seconds": 600,      // optional
  "artifacts": ["coverage.xml"]// optional
}
```

### Response

```jsonc
{
  "run_id": "...",
  "status": "passed",          // passed | failed | error
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "duration_ms": 1234,
  "artifacts": [{ "name": "coverage.xml", "url": "https://..." }]
}
```

Normalized into `gitpilot.sandbox_providers.base.SandboxResult`. A
`SandboxResult` with `skipped=True` indicates the sandbox was not run (e.g.
unreachable and not required in a dry-run).
