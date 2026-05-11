"""Tests for the Batch P4-E supply-chain artefacts.

Two kinds of checks live here:

* the SBOM generator (``scripts/sbom_fallback.py``) produces a valid
  CycloneDX 1.5 JSON document;
* the supply-chain GitHub Actions workflow is well-formed (steps in
  the expected order, OIDC permissions, dry-run + release branches).

Neither test runs the release workflow itself — that's the job of the
``workflow_dispatch`` dry-run on GitHub.  These checks are the cheapest
local guard against accidentally breaking the file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SBOM_SCRIPT = REPO_ROOT / "scripts" / "sbom_fallback.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "supply-chain.yml"


# ----------------------------------------------------------------------
# SBOM generator
# ----------------------------------------------------------------------

def _run_sbom() -> dict:
    completed = subprocess.run(
        [sys.executable, str(SBOM_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def sbom() -> dict:
    return _run_sbom()


def test_sbom_is_cyclonedx_15(sbom: dict) -> None:
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["version"] == 1
    assert sbom["serialNumber"].startswith("urn:uuid:")


def test_sbom_metadata_carries_root_component_and_timestamp(sbom: dict) -> None:
    meta = sbom["metadata"]
    assert "timestamp" in meta
    assert meta["component"]["type"] == "application"
    assert meta["component"]["name"] == "gitcopilot"
    assert meta["component"]["purl"].startswith("pkg:pypi/gitcopilot@")


def test_sbom_components_are_sorted_and_unique(sbom: dict) -> None:
    components = sbom["components"]
    assert components, "expected at least one component"
    names = [c["name"].lower() for c in components]
    assert names == sorted(names)
    # purls are unique per (name, version) pair.
    purls = [c["purl"] for c in components]
    assert len(purls) == len(set(purls))


def test_sbom_every_component_has_purl_name_version(sbom: dict) -> None:
    for comp in sbom["components"]:
        assert comp["type"] == "library"
        assert comp["name"]
        assert comp["version"]
        assert comp["purl"].startswith("pkg:pypi/")


# ----------------------------------------------------------------------
# Workflow shape
# ----------------------------------------------------------------------

def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file()


def _load_workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return _load_workflow_text()


def test_workflow_declares_required_permissions(workflow_text: str) -> None:
    # OIDC token is required for Sigstore keyless signing.
    assert "id-token: write" in workflow_text
    # Release upload requires contents:write.
    assert "contents: write" in workflow_text


def test_workflow_runs_on_release_and_workflow_dispatch(workflow_text: str) -> None:
    assert "release:" in workflow_text
    assert "workflow_dispatch:" in workflow_text


def test_workflow_uses_pinned_sigstore_action(workflow_text: str) -> None:
    # Pinning a known-good Sigstore action version is part of the
    # supply-chain story — any version drift goes through a PR.
    assert "sigstore/gh-action-sigstore-python@v3.0.0" in workflow_text


def test_workflow_orders_steps_build_then_sbom_then_sign(workflow_text: str) -> None:
    build_idx = workflow_text.index("python -m build")
    sbom_idx = workflow_text.index("sbom_fallback.py")
    sign_idx = workflow_text.index("Sign distributions with Sigstore")
    assert build_idx < sbom_idx < sign_idx, (
        "expected build → SBOM → sign step order"
    )


def test_workflow_uploads_sbom_to_release(workflow_text: str) -> None:
    assert "artefacts/sbom.json" in workflow_text
    assert "softprops/action-gh-release" in workflow_text


def test_workflow_has_dry_run_path(workflow_text: str) -> None:
    # workflow_dispatch path uploads as an actions artefact rather than
    # the release, so engineers can verify the chain without cutting a tag.
    assert "github.event_name != 'release'" in workflow_text
    assert "actions/upload-artifact" in workflow_text


# ----------------------------------------------------------------------
# Make targets exist
# ----------------------------------------------------------------------

def test_make_targets_present() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("sbom:", "sbom-verify:", "audit-npm:", "linkcheck:"):
        assert target in makefile, f"missing Makefile target: {target}"
