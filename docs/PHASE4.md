# Phase 4 — Quality safety net

Three additive batches that lock the contract, tidy the docs, and harden
the release pipeline.  Every change is reversible in a single revert.

## Status

| Batch | Done | Notes |
|---|---|---|
| P4-C · Public API stability layer | ✅ | `gitpilot/_deprecation.py`, `docs/API_STABILITY.md`, stronger `tests/test_public_api.py` |
| P4-D · README rewrite + docs site | ✅ | one-path README; legacy deployment docs moved to `docs/deploy/`; `mkdocs.yml` + `make docs-{serve,build}`; in-repo link checker |
| P4-E · Supply chain                | ✅ | `make sbom` (CycloneDX 1.5), Sigstore-signing release workflow, `make audit-npm` baseline |

Full test count: **1 266 passing** (1 194 prior + 72 new).
Gated coverage: **88.70 %** across 21 modules.
Strict mypy: **22 source files clean**.

---

## P4-C — Public API stability

* **`gitpilot/_deprecation.py`** — small helper exporting
  `deprecated(...)` (decorator) and `deprecated_alias(...)` (factory).
  Both emit a single `DeprecationWarning` per process per symbol,
  carry `__gitpilot_deprecated__` metadata for tooling, and follow a
  fixed warning template (`"<old> is deprecated; use <new> instead
  (will be removed in v<X.Y>)"`).
* **`docs/API_STABILITY.md`** — the written contract: what
  `gitpilot.public_api` guarantees, the SemVer mapping, the migration
  playbook (treat `DeprecationWarning` as a hard build break).
* **`tests/test_public_api.py`** now enforces three extra invariants:
  every name resolves, every callable carries a non-trivial
  docstring, every callable has resolvable type hints.

No public symbol is currently scheduled for removal.  The first real
deprecation will use:

```python
from gitpilot._deprecation import deprecated_alias
parse_mentions = deprecated_alias(
    "parse_mentions", expand_mentions,
    replacement="gitpilot.public_api.expand_mentions",
    removed_in="2.0",
)
```

## P4-D — README + docs site

* **README** — one path, three commands.  Everything heavier moves to
  `docs/`.
* **`docs/deploy/`** — 10 legacy deployment docs moved verbatim
  (history preserved via `git mv`):

  ```
  docker.md  render.md  render-detailed.md  vercel.md  vercel-setup.md
  vercel-testing.md  quick.md  production.md  production-mcp.md  install-mcp.md
  ```

* **`docs/contributing/`** — packaging + frontend reference.
* **`mkdocs.yml`** — material theme; `make docs-serve` runs locally,
  `make docs-build --strict` is CI-ready.
* **`tests/test_docs_links.py`** — broken-link checker for in-repo
  markdown.  Failing test = "you moved a file without updating its
  incoming links."  Three real broken links were caught and fixed by
  this batch.

## P4-E — Supply chain

* **`scripts/sbom_fallback.py`** — dependency-light CycloneDX 1.5 SBOM
  generator.  Walks `importlib.metadata` to produce a deterministic,
  sorted, JSON SBOM that downstream consumers (Sigstore attestations,
  vendor risk tools) can consume as-is.
* **`make sbom`** / **`make sbom-verify`** — produces and validates
  `artefacts/sbom.json` (192 components for the current dev env).
* **`make audit-npm`** — gates the frontend on `npm audit` at
  `--audit-level=high`; baseline locked.
* **`.github/workflows/supply-chain.yml`** — separate workflow that
  runs after a GitHub Release:
  1. builds wheel + sdist,
  2. generates SBOM,
  3. **signs every distribution with Sigstore via keyless OIDC**
     (pinned to `sigstore/gh-action-sigstore-python@v3.0.0`),
  4. uploads SBOM + `.sigstore.json` signatures back to the release.
  Workflow-dispatch dry-runs upload to an Actions artefact instead of
  the release, so engineers can verify the chain without cutting a tag.
* **`tests/test_supply_chain.py`** — 12 assertions: SBOM is valid
  CycloneDX 1.5, components are sorted + unique, every component
  has `purl`/`name`/`version`; the workflow has the right OIDC
  permissions, the right step order, the right Sigstore action pin,
  and a dry-run path.

## Rollback

| Batch | One-line rollback |
|---|---|
| P4-C | `git rm gitpilot/_deprecation.py docs/API_STABILITY.md tests/test_deprecation.py` (or `git revert <sha>`) |
| P4-D | Single `git revert` restores the old README and `docs/deploy/` layout |
| P4-E | `rm .github/workflows/supply-chain.yml scripts/sbom_fallback.py tests/test_supply_chain.py` |

Each batch is independent, so a partial revert is supported.
