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


@pytest.mark.parametrize("path", ["/.env", "/.env.production", "/.git/config", "/.streamlit/secrets.toml",
                                  "/file%3D../.env", "/wp-login.php", "/backup.zip", "/assets-old/app.js"])
def test_scanner_paths_get_a_plain_404_not_the_app(client, path):
    resp = client.get(path)
    assert resp.status_code == 404
    assert "root" not in resp.text


def test_root_build_files_are_served_as_files(client, tmp_path):
    (tmp_path / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    resp = client.get("/robots.txt")
    assert resp.status_code == 200 and resp.text.startswith("User-agent")
    assert "text/plain" in resp.headers["content-type"]


def test_head_on_the_app_is_allowed(client):
    assert client.head("/").status_code == 200
    assert client.head("/auth").status_code == 200


def test_hashed_assets_are_immutable_and_a_missing_one_clears_a_stale_cache(tmp_path):
    from fastapi import FastAPI

    from gitpilot.web_static import BuildAssets

    (tmp_path / "index-NEW.css").write_text("body{}", encoding="utf-8")
    app = FastAPI()
    app.mount("/assets", BuildAssets(directory=tmp_path), name="assets")
    c = TestClient(app)
    hit = c.get("/assets/index-NEW.css")
    assert hit.status_code == 200 and "immutable" in hit.headers["cache-control"]
    miss = c.get("/assets/index-OLD.css")                  # requested by a cached old page
    assert miss.status_code == 404
    assert miss.headers["clear-site-data"] == '"cache"'
