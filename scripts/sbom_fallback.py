#!/usr/bin/env python
"""Minimal CycloneDX SBOM generator (Batch P4-E).

This is a fallback for environments where ``cyclonedx-py`` isn't
installed.  It produces a valid CycloneDX 1.5 JSON SBOM by walking
``importlib.metadata`` for every installed distribution in the active
environment.

Output goes to stdout; ``make sbom`` redirects it to
``artefacts/sbom.json``.  The schema is the same one `cyclonedx-py`
emits, so downstream consumers (Sigstore attestations, vendor
risk tools) don't have to special-case it.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from importlib import metadata as md
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-")


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{_normalise(name)}@{version}"


def _licenses(dist: md.Distribution) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    meta = dist.metadata
    for value in meta.get_all("License-Expression") or []:
        out.append({"expression": str(value)})
    for value in meta.get_all("License") or []:
        v = str(value).strip()
        if v and v.lower() != "unknown":
            out.append({"license": {"name": v}})
    return out


def _component_from(dist: md.Distribution) -> Dict[str, Any]:
    name = dist.metadata["Name"] or "unknown"
    version = dist.metadata["Version"] or "0"
    component: Dict[str, Any] = {
        "type": "library",
        "bom-ref": _purl(name, version),
        "name": name,
        "version": version,
        "purl": _purl(name, version),
    }
    licenses = _licenses(dist)
    if licenses:
        component["licenses"] = licenses
    return component


def _root_component() -> Dict[str, Any]:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    name = "gitcopilot"
    version = "unknown"
    try:
        text = pyproject.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version =") and "=" in stripped:
                version = stripped.split("=", 1)[1].strip().strip("'\"")
                break
    except OSError:
        pass
    return {
        "type": "application",
        "bom-ref": _purl(name, version),
        "name": name,
        "version": version,
        "purl": _purl(name, version),
    }


def _emit(components: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [{
                "vendor": "gitpilot",
                "name": "sbom_fallback",
                "version": "1.0",
            }],
            "component": _root_component(),
        },
        "components": sorted(
            (_component_from(d) for d in components),
            key=lambda c: c["name"].lower(),
        ),
    }


def main() -> int:
    distributions = list(md.distributions())
    bom = _emit(distributions)
    json.dump(bom, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - script
    raise SystemExit(main())
