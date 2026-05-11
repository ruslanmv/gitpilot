# Phase 2 — Performance

Five additive batches that target perceived speed and per-turn cost
without changing any user-visible behaviour by default.  Every code
path is reachable only when its feature flag is on; the flags ship
**off** so the merge is risk-free.

## Status

| Batch | Done | Flag | Notes |
|---|---|---|---|
| P2-A · Prompt cache builder           | ✅ | `prompt_cache`    | Anthropic-only ``cache_control: ephemeral`` markers |
| P2-B · Lazy MCP tool defs             | ✅ | `lazy_tool_defs`  | drops tools the mode policy forbids |
| P2-C · Context-pack memoisation       | ✅ | `context_cache`   | LRU keyed on workspace, mode, query, mtimes |
| P2-D · End-to-end SSE streaming       | ✅ | `stream_v2`, `ui_stream_v2` | new `/chat/stream` route, legacy unchanged |
| P2-E · Model warmup                   | ✅ | `model_warmup`    | 1-token startup ping with 3-second cap |

Test count: **1 172 passing** (1 109 prior + 63 new).
Gated coverage: **88.79 %** across 19 modules.
Strict mypy: **20 source files clean**.

## Turning a flag on

```bash
# Single env-var override, scoped to the process
GITPILOT_FLAGS="prompt_cache=1,lazy_tool_defs=1,context_cache=1,stream_v2=1,model_warmup=1" \
  gitpilot serve

# Per-workspace persistence
cat > .gitpilot/flags.json <<'EOF'
{
  "prompt_cache":   true,
  "lazy_tool_defs": true,
  "context_cache":  true,
  "stream_v2":      true,
  "model_warmup":   true
}
EOF
```

## Bench DoD

The plan asked for two measurable gates before flipping flags on in
production.  Both checks are easy to wire into a smoke job:

* **Input tokens ↓ ≥ 50 %** on a 20-turn benchmark with `prompt_cache=1`.
  Measure with the digest emitted by ``SystemPayload.cache_prefix_digest``
  and your provider's input-token billing field.
* **p50 first-byte ↓ ≥ 40 %** on a fixed prompt with `stream_v2=1`.
  The `done` event payload includes ``first_byte_ms`` so the benchmark
  can record it directly.

## Quick reference

### Prompt cache

```python
from gitpilot.public_api import build_system_blocks, to_anthropic_kwargs

payload = build_system_blocks(
    base_system="You are GitPilot.",
    workspace=workspace_path,
    mode_slug="coder",
    tool_defs=list_tools_for_session(),
    session_conventions=current_turn_notes,
)
kwargs = to_anthropic_kwargs(payload)   # ``system=`` ready for the SDK
```

### Lazy MCP tool defs

```python
from gitpilot.public_api import prune_descriptors, build_mcp_agent_tools
# Mode picker → ToolPolicy → bridge accepts policy=
crewai_tools = build_mcp_agent_tools(policy=active_mode.tool_policy())
```

### Context cache

```python
from gitpilot.public_api import build_context_cached, get_context_cache_stats
context = build_context_cached(workspace_path, query=user_query, mode_slug="coder")
print(get_context_cache_stats().hit_ratio)
```

### SSE streaming

Server side (one-line registration, idempotent):

```python
from gitpilot.public_api import register_stream_routes
register_stream_routes(app, adapter=my_adapter)
```

Client side (browser):

```js
const es = new EventSource('/chat/stream', { withCredentials: true });
es.addEventListener('assistant_chunk', (e) => render(JSON.parse(e.data).text));
es.addEventListener('done',            (e) => es.close());
```

### Model warmup

```python
from gitpilot.public_api import register_warmup
register_warmup(app)   # noop when flag off; idempotent across reloads
```

## Rollback paths

| Issue | Action |
|---|---|
| Anthropic cache markers break a provider | `GITPILOT_FLAGS="prompt_cache=0"` |
| Mode policy hides a tool we still need | `GITPILOT_FLAGS="lazy_tool_defs=0"` |
| Stale context served from the LRU | `GITPILOT_FLAGS="context_cache=0"` or call `clear_context_cache()` |
| Streaming UX flakier than batch | `GITPILOT_FLAGS="stream_v2=0"` (legacy routes still serve) |
| Warmup timeouts during boot storm | `GITPILOT_FLAGS="model_warmup=0"` |

Each item is one env-var change; no redeploy required.

## Backwards compatibility

* No existing module deleted or rewritten.  The few legacy files that
  were touched (`gitpilot/api.py`, `gitpilot/cli.py`, `gitpilot/llm_provider.py`,
  `gitpilot/agent_executor.py`, `gitpilot/mcp_tools_bridge.py`) received
  **only additive changes**: new helpers, new optional arguments
  defaulting to legacy behaviour, new co-methods.  Every legacy entry
  point keeps its signature.
* The 1 109 pre-existing tests continue to pass alongside the 63 new
  ones.
* All new modules live behind feature flags that default off; turning
  them on is one env-var change.
