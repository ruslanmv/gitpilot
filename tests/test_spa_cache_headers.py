"""index.html must never be served from a stale cache.

It names the content-hashed bundle of one build. Without ``Cache-Control``
browsers cache it heuristically from ``Last-Modified`` and, after a deploy,
keep loading a previous build whose CSS/JS no longer exist (the sign-in page
then renders unstyled on the Hugging Face Space).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import gitpilot._api_app as api_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    monkeypatch.setattr(api_app, "STATIC_DIR", tmp_path)
    return TestClient(api_app.app)


@pytest.mark.parametrize("path", ["/", "/auth", "/auth/reset-password?token=x", "/workspace"])
def test_index_html_is_always_revalidated(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "id=\"root\"" in resp.text
    assert resp.headers["cache-control"] == "no-cache"


def test_api_paths_are_not_the_spa(client):
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
