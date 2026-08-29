# Compatibility: matrix-builder, matrix-designer, SelfRepair

An assessment of how three repositories that all delegate coding to GitPilot fit
together, and where they do not. Read at the commits below; every claim is anchored to
a file and line in those trees.

| Repository | Commit | Role |
|---|---|---|
| [`agent-matrix/matrix-builder`](https://github.com/agent-matrix/matrix-builder) | `b1d72b9` | Control plane: idea → signed bundle → validated result |
| [`agent-matrix/matrix-designer`](https://github.com/agent-matrix/matrix-designer) | `371da2a` | Design brain: idea + blueprint → design bundle |
| [`ruslanmv/SelfRepair`](https://github.com/ruslanmv/SelfRepair) | `de95d57` | Delivery copilot: scan repos → repair plan → delegate |

---

## The short answer

**matrix-designer ↔ matrix-builder are genuinely integrated**, and share a real
contract: `design-bundle.schema.json` is byte-for-byte identical in both trees, same
`$id`, same `schema_version` const `"matrix.designer.bundle/v1"`. Designer produces,
builder consumes over `POST /design/blueprints` and `/design/refine`, persists into a
`design_bundles` table, and co-hosts the designer process in one Hugging Face Space.
That is a working producer/consumer pair with five specific defects listed below,
mostly of the "documented but not implemented" kind.

**SelfRepair is on a separate island.** Neither Matrix repo references it and it
references neither of them. Its contract owner is a *fourth* repository,
`agent-matrix/matrix-maintainer`. It shares exactly two things with the others: a
dependency on GitPilot, and the `agent-matrix` GitHub org. Treating it as part of the
same pipeline would be a decision to make, not a fact to discover.

**All three delegate to GitPilot, and no two agree on how.** This is the finding that
matters most for GitPilot, and it is the one thing GitPilot itself can fix.

---

## What each one is

### matrix-builder — the control plane

Python 3.11+ FastAPI backend plus a pnpm/Next.js 15 monorepo (`matrix-builder`
v0.8.0b8; web app `@ruslanmv/matrix-builder-web`). Not an importable Python package —
`packages = []` in `pyproject.toml:26-28`.

Turns a one-sentence idea into a signed **Matrix Bundle** — blueprint, locked
standards, an allowed-files scope, acceptance criteria — hands that contract to an
external AI coder, and validates the result as `approved` / `needs-repair` /
`rejected`. It positions GitPilot as the coder, `agent-generator` as the engine,
`matrix-definitions` as the standards and MatrixHub as the registry
(`README.md:96-104`).

Exposes REST under `/api/v1` (`services/api/app/api/router.py:5-20`): `/ideas/parse`,
`/blueprints/*`, `/bundles/{id}/*` including `/manifest`, `/validate`,
`/publish-to-matrixhub` and `/prompt/{coder}`, plus `/bundles/{id}/gitpilot/runs` and
`/gitpilot/runs/{run_id}/{diff,logs}`. It also ships a JSON-Schema contract registry:
`packages/contracts/schema-registry.json`, `schema_version
"matrix.builder.contracts/v1"`, eleven schemas.

No LLM framework in-process. All generation is delegated.

### matrix-designer — the brain

Pure Python, `src/` layout, `requires-python >=3.10`. Console scripts `mdesign` and
`matrix-designer-service`; FastAPI on `:8077` with `/design/{blueprints,refine,bundle,review}`
(`src/matrix_designer/service.py:313-330`).

Turns an idea plus a chosen blueprint into a governed **Design Bundle**: framework
decision, visual target, architecture, contracts, asset manifest, acceptance, an
ordered batch roadmap, governance, provenance.

It is also **the only one of the three with an MCP server** — stdio, nine tools:
`analyze_idea`, `decompose_reference`, `propose_architecture`, `generate_batches`,
`assemble_design_bundle`, `validate_design`, `export_to_builder`,
`generate_blueprints`, `refine_design` (`src/matrix_designer/mcp_server.py:39-101`).

LangGraph `StateGraph` primary, CrewAI alternative, and a deterministic runner when no
API key is set — so it degrades to something usable offline.

### SelfRepair — the delivery copilot

Python 3.11–3.12, `selfrepair-repo` v1.0.0. Typer CLI `selfrepair-repo`, FastAPI at
`backend.app.main:app`. Discovers repositories across GitHub/GitLab/Hugging Face,
scans delivery readiness, classifies issues, produces a repair plan, delegates
code-writing to GitPilot and validation to MatrixLab.

It never writes code itself, and says so
(`selfrepair/planning/repair_plan.py:3-5`). Its `agent-card.json` advertises a
JSON-RPC surface at `/v1/rpc` with four methods: `selfrepair.{scan,repair,validate,report}`.

No agent framework — raw `httpx` against an OpenAI-compatible endpoint, default
OllaBridge, default model `qwen2.5:1.5b` (`selfrepair/settings.py:64-74`). Which is
the same model tier GitPilot's LITE dialect exists for.

---

## Where they meet, and where they don't

### The one real shared contract

`design-bundle.schema.json` is identical in
`matrix-builder/packages/contracts/schemas/` and
`matrix-designer/src/matrix_designer/_data/schemas/` — `diff` clean, same `$id`
(`https://matrixhub.io/schemas/matrix-designer/design-bundle.schema.json`), same
`schema_version` const. matrix-builder registers matrix-designer as a co-owner of its
contract registry (`schema-registry.json:65`). This is the integration working.

### Five defects in that pair

1. **Batch acceptance key disagreement, inside the same namespace.**
   `design-bundle.schema.json` requires `batch_roadmap[].acceptance` with
   `additionalProperties: false`; `blueprint-details.schema.json` requires
   `batches[].acceptance_criteria`. A `blueprint-details` batch object fails
   `design-bundle` validation, and `exporter.py:48` translates by hand. Both schemas
   carry the `matrix-designer` `$id` namespace, so this is one namespace holding two
   incompatible spellings of the same field.

2. **`blueprint-details.schema.json` is single-sided.** It exists only in
   matrix-builder. The producer cannot validate its own `details` payload.

3. **Designer's documented builder extensions are not implemented.**
   `docs/INTEGRATION.md:17-23` requires `design_mode` and `references[]` on
   `idea-request`, and `design_bundle_ref` and `design_digest` on
   `blueprint-candidate`. `exporter.py:23-33` emits exactly those four fields.
   matrix-builder's schemas contain none of them, nor do its Pydantic models — so
   **exported provenance is silently dropped**.

4. **Designer's documented builder endpoint is absent.** `docs/INTEGRATION.md:30`
   specifies `POST /api/v1/design`. matrix-builder mounts no `/design` route; the
   equivalents live under `/blueprints/*`.

5. **Version metadata is internally inconsistent in both.** matrix-builder carries
   three versions for one release — `VERSION`/`package.json` `0.9.0-batch.9`,
   `pyproject.toml` `0.8.0b8`, `schema-registry.json` `0.7.0-batch.7`. matrix-designer
   reports `0.6.2` from `VERSION`/`__init__.py` but `0.1.0` in `pyproject.toml:6`, so
   **the built wheel is mis-versioned**.

### Three cross-cutting mismatches

6. **GitPilot transport conflict — the important one.** The two consumers cannot share
   one GitPilot deployment config:

   | | matrix-builder | SelfRepair |
   |---|---|---|
   | Path | `POST /api/v1/gitpilot/runs` | `POST /v1/agents/repair` |
   | Auth | `X-A2A-Secret` header | `Authorization: Bearer` |
   | Payload | signed short-TTL `bundle_url` | inline repair-plan JSON |
   | Response | polled (`/diff`, `/logs`) | SSE stream |
   | Default host | `https://ruslanmv-gitpilot.hf.space` | `http://localhost:9000`, also `http://gitpilot:8000` |
   | Also | — | `gitpilot run --repo … --prompt …` subprocess |

   Anchors: `services/api/app/services/gitpilot_run_service.py:86-137`;
   `selfrepair/connectors/gitpilot.py:104-112`,
   `selfrepair/coders/gitpilot_client.py:8,119-151`, `selfrepair/gitpilot/client.py:20`,
   `selfrepair/worker/settings.py:15`.

7. **Allowlist key conflict for the same concept.** matrix-builder and matrix-designer
   scope edits with `allowed_files` + `must_not_change`; SelfRepair uses
   `allowed_paths` + `forbidden_paths` (`selfrepair/planning/repair_plan.py:14-17`).
   Same idea, two vocabularies, and GitPilot has to honour whichever it is handed.

8. **Python floor spread.** designer `>=3.10`, builder `>=3.11`, SelfRepair
   `>=3.11,<3.13`. Since builder imports `matrix_designer` **in-process**
   (`designer_client.py:65`), the effective floor is 3.11 and designer's advertised
   3.10 support is untested on the path that matters. Dependency floors also drift
   (`jsonschema` 4.20 vs 4.22; `fastapi` 0.110 vs 0.115; `pydantic` 2 vs 2.8) — nothing
   is pinned, so it resolves, but designer installed standalone does not get builder's
   stricter floors.

9. **The MCP surface is built and unconsumed.** matrix-designer ships nine MCP tools.
   matrix-builder has no MCP client or server — only an unstarted backlog item
   (`docs/BATCH_BACKLOG.md:82`, "MCP-01") — and reaches the designer over plain HTTP or
   an in-process import instead. SelfRepair has no MCP at all: zero hits across its
   Python, JSON, YAML, TOML and Markdown.

---

## What this means for GitPilot

Three consumers, three transports, and GitPilot is the one component that could make
that one transport. The v4 engine has most of what is needed already:

**The MCP surface is the shortest path.** Batch V4-H1 made GitPilot an MCP *client*
that registers `mcp.<server>.<tool>` entries carrying the server's real `inputSchema`.
matrix-designer already speaks MCP with nine tools and nobody consuming them.
Pointing GitPilot's `mcp.json` at `matrix-designer` is a config change, not a
development project, and it turns the designer's tools into things an agent can call —
which is what the designer built them for.

**Topology documents are the natural home for a bundle's constraints.** A Matrix
Bundle's `allowed_files` / `must_not_change`, and SelfRepair's `allowed_paths` /
`forbidden_paths`, are both the same thing GitPilot's capability qualifiers already
express:

```yaml
capabilities:
  fs.write: { paths: ["src/**"], exclude: ["src/generated/**"] }
  git.commit: ask
  git.push: false
```

A caller that today ships a bespoke allowlist could ship a topology document instead,
and get the enforcement, the approval rules and the audit journal with it. That is a
smaller change for them than for us, and it collapses defect 7 into one vocabulary.

**The transport conflict is worth resolving on GitPilot's side, once.** Two auth
schemes and two framings for "run a coding task" is not a disagreement either consumer
can settle alone. The v2 SSE stream plus `POST /api/v2/agent/resume` already covers
SelfRepair's streaming shape *and* matrix-builder's polling shape — a resumable run
with an event stream can be read either way. Publishing that as the one supported
contract, with the bundle passed either inline or by signed URL, would let both
consumers converge without either of them changing their model of the world.

**SelfRepair's default model is `qwen2.5:1.5b`.** That is precisely the tier GitPilot's
LITE dialect exists to serve, and precisely the tier the §18 benchmark gate has not yet
been measured on. If SelfRepair is a real consumer, that measurement is not optional
housekeeping — it is the thing that decides whether the delegation works at all.

### Not assessed

Runtime behaviour. Nothing here was executed: the three repositories were read at the
commits above, not deployed. Claims about *what the code says* are anchored; claims
about what a live pipeline does are not made.
