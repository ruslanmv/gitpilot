"""Provider configuration: what each provider requires, and where it points.

Covers the three defects behind the "OLLABRIDGE API key not configured"
startup warning and the model-discovery failures that followed it:

* the CLI re-derived per-provider rules instead of asking settings;
* OllaBridge defaulted to port 8000, which is GitPilot's own backend;
* endpoint URLs were used verbatim, so a pasted operation path 404'd.
"""
from __future__ import annotations

import importlib

import pytest

from gitpilot import settings as settings_mod
from gitpilot.settings import (
    OLLABRIDGE_DEFAULT_BASE_URL,
    OPENWEBUI_API_SUFFIX,
    AppSettings,
    LLMProvider,
    OllaBridgeConfig,
    openai_compatible_api_base,
    points_at_gitpilot_server,
)


@pytest.fixture(autouse=True)
def default_server_port(monkeypatch):
    """Pin GitPilot's own port for the duration of each test.

    ``serve`` publishes the port it bound into the environment, and a suite
    that invokes ``serve`` in-process leaves that behind. Asserting against
    whatever a previous test happened to bind is not a test of anything, so
    the ambient value is cleared and the default applies.
    """
    monkeypatch.delenv("GITPILOT_SERVER_PORT", raising=False)
    monkeypatch.delenv("GITPILOT_PORT", raising=False)


@pytest.fixture
def fresh_settings(tmp_path, monkeypatch) -> AppSettings:
    """An AppSettings backed by a throwaway config dir."""
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_FILE", tmp_path / "settings.json")
    return AppSettings()


# ── Which providers actually need a key ───────────────────────────────────


@pytest.mark.parametrize(
    "provider, expected",
    [
        (LLMProvider.ollama, True),
        (LLMProvider.ollabridge, True),
        (LLMProvider.openai, False),
        (LLMProvider.claude, False),
        (LLMProvider.watsonx, False),
        (LLMProvider.custom, False),
    ],
)
def test_is_provider_configured_defaults(fresh_settings, provider, expected):
    """Local and gateway providers are usable out of the box; key ones are not."""
    fresh_settings.provider = provider
    assert fresh_settings.is_provider_configured() is expected


def test_ollabridge_never_reports_a_missing_api_key(fresh_settings):
    """The regression this whole change starts from.

    OllaBridge is an open gateway; demanding a key made a working default
    install look broken on every startup.
    """
    fresh_settings.provider = LLMProvider.ollabridge
    fresh_settings.ollabridge.api_key = ""
    assert fresh_settings.is_provider_configured() is True


def test_openwebui_needs_only_a_url(fresh_settings):
    fresh_settings.provider = LLMProvider.openwebui
    assert fresh_settings.is_provider_configured() is True

    fresh_settings.openwebui.base_url = ""
    assert fresh_settings.is_provider_configured() is False


def test_custom_endpoint_needs_a_url_and_a_model(fresh_settings):
    """Nothing to fall back on, so both halves are required."""
    fresh_settings.provider = LLMProvider.custom
    assert fresh_settings.is_provider_configured() is False

    fresh_settings.custom.base_url = "https://inference.example.com/v1"
    assert fresh_settings.is_provider_configured() is False

    fresh_settings.custom.model = "some-model"
    assert fresh_settings.is_provider_configured() is True


def test_cli_startup_check_uses_the_canonical_rule(monkeypatch, fresh_settings):
    """The CLI banner must not re-implement per-provider requirements."""
    cli = importlib.import_module("gitpilot.cli")
    fresh_settings.provider = LLMProvider.ollabridge
    monkeypatch.setattr(cli, "get_settings", lambda: fresh_settings)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

    _, _, configured, issues, _ = cli._check_configuration()

    assert configured is True
    assert not any("API key" in issue for issue in issues)


def test_cli_startup_check_still_flags_a_missing_key(monkeypatch, fresh_settings):
    cli = importlib.import_module("gitpilot.cli")
    fresh_settings.provider = LLMProvider.claude
    fresh_settings.claude.api_key = ""
    monkeypatch.setattr(cli, "get_settings", lambda: fresh_settings)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

    _, _, configured, issues, warnings = cli._check_configuration()

    assert configured is False
    assert any("CLAUDE" in issue for issue in issues)
    assert any("ANTHROPIC_API_KEY" in w for w in warnings)


# ── GitPilot's own address is not a provider address ──────────────────────


def test_ollabridge_default_is_not_gitpilots_port():
    """The default must not resolve to GitPilot itself."""
    assert OllaBridgeConfig().base_url == OLLABRIDGE_DEFAULT_BASE_URL
    assert not points_at_gitpilot_server(OLLABRIDGE_DEFAULT_BASE_URL)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://localhost:8000", True),
        ("http://127.0.0.1:8000", True),
        ("http://0.0.0.0:8000", True),
        ("http://localhost:11435", False),
        ("http://example.com:8000", False),
        ("https://api.openai.com", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_points_at_gitpilot_server(url, expected):
    assert points_at_gitpilot_server(url) is expected


def test_points_at_gitpilot_server_follows_the_bound_port(monkeypatch):
    """`serve` publishes the port it actually bound, which may not be 8000."""
    monkeypatch.setenv("GITPILOT_SERVER_PORT", "8123")
    assert points_at_gitpilot_server("http://127.0.0.1:8123") is True
    assert points_at_gitpilot_server("http://127.0.0.1:8000") is False


def test_saving_a_self_referential_ollabridge_url_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_mod, "_settings", AppSettings())

    with pytest.raises(ValueError, match="own server address"):
        settings_mod.update_settings({"ollabridge": {"base_url": "http://localhost:8000"}})


def test_a_legacy_self_referential_url_is_repaired_on_load(monkeypatch, tmp_path):
    """Installs predating the default change carry the bad URL on disk."""
    config = tmp_path / "settings.json"
    config.write_text(
        AppSettings().model_copy(
            update={"ollabridge": OllaBridgeConfig(base_url="http://localhost:8000")}
        ).model_dump_json(),
        "utf-8",
    )
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_FILE", config)
    monkeypatch.delenv("OLLABRIDGE_BASE_URL", raising=False)

    loaded = AppSettings.from_disk()

    assert loaded.ollabridge.base_url == OLLABRIDGE_DEFAULT_BASE_URL


def test_model_discovery_explains_a_self_referential_url(fresh_settings):
    from gitpilot.model_catalog import list_models_for_provider

    fresh_settings.ollabridge.base_url = "http://127.0.0.1:8000"
    models, error = list_models_for_provider(LLMProvider.ollabridge, fresh_settings)

    assert models == []
    assert "GitPilot's own server address" in error
    assert OLLABRIDGE_DEFAULT_BASE_URL in error


# ── URL normalisation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, suffix, expected",
    [
        # An instance root gains the API path.
        ("https://x.example.com", "/v1", "https://x.example.com/v1"),
        ("https://x.example.com/", "/v1", "https://x.example.com/v1"),
        # An API root is left alone.
        ("https://x.example.com/v1", "/v1", "https://x.example.com/v1"),
        ("https://x.example.com/openai/v1", "/v1", "https://x.example.com/openai/v1"),
        # A pasted operation path is trimmed back to its root — this is what
        # people copy out of a gateway's documentation.
        (
            "https://x.example.com/v1/chat/completions",
            "/v1",
            "https://x.example.com/v1",
        ),
        ("https://x.example.com/v1/models", "/v1", "https://x.example.com/v1"),
        # Open WebUI hangs its OpenAI-compatible surface off /api.
        ("http://localhost:3000", OPENWEBUI_API_SUFFIX, "http://localhost:3000/api"),
        ("http://localhost:3000/api", OPENWEBUI_API_SUFFIX, "http://localhost:3000/api"),
        ("", "/v1", ""),
    ],
)
def test_openai_compatible_api_base(raw, suffix, expected):
    assert openai_compatible_api_base(raw, suffix) == expected


# ── Published model catalogues ────────────────────────────────────────────


def test_catalog_document_provider_map_shape():
    """A gateway that publishes a provider map, e.g. a well-known document."""
    from gitpilot.model_catalog import _models_from_catalog_document

    doc = {
        "provider": {
            "gateway": {
                "name": "Gateway",
                "options": {"baseURL": "https://inference.example.com/v1"},
                "models": {
                    "model-large": {"name": "Large", "limit": {"context": 262144}},
                    "model-small": {"name": "Small"},
                },
            }
        }
    }
    assert _models_from_catalog_document(doc) == ["model-large", "model-small"]


@pytest.mark.parametrize(
    "doc, expected",
    [
        ({"data": [{"id": "a"}, {"id": "b"}]}, ["a", "b"]),
        ({"models": ["x", "y"]}, ["x", "y"]),
        ({"models": {"m1": {}, "m2": {}}}, ["m1", "m2"]),
        (["p", "q"], ["p", "q"]),
        ({"unrelated": 1}, []),
        ("not a document", []),
    ],
)
def test_catalog_document_other_shapes(doc, expected):
    from gitpilot.model_catalog import _models_from_catalog_document

    assert _models_from_catalog_document(doc) == expected


def test_custom_models_prefer_a_published_catalog(fresh_settings, monkeypatch):
    """The richer source wins, and the fallback is not called needlessly."""
    from gitpilot import model_catalog

    fresh_settings.custom.base_url = "https://inference.example.com/v1"
    monkeypatch.setattr(
        model_catalog, "_list_wellknown_models", lambda *a, **k: ["published-model"]
    )

    def fail(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("fell back despite a published catalogue")

    monkeypatch.setattr(model_catalog, "_list_openai_compatible_models", fail)

    models, error = model_catalog.list_models_for_provider(
        LLMProvider.custom, fresh_settings
    )
    assert models == ["published-model"]
    assert error is None


def test_custom_models_fall_back_to_the_openai_listing(fresh_settings, monkeypatch):
    from gitpilot import model_catalog

    fresh_settings.custom.base_url = "https://inference.example.com"
    monkeypatch.setattr(model_catalog, "_list_wellknown_models", lambda *a, **k: [])
    seen = {}

    def fake_listing(api_base, api_key, headers=None, label="endpoint"):
        seen["api_base"] = api_base
        seen["headers"] = headers
        return ["listed-model"], None

    monkeypatch.setattr(model_catalog, "_list_openai_compatible_models", fake_listing)
    fresh_settings.custom.headers = {"x-user": "me@example.com"}

    models, error = model_catalog.list_models_for_provider(
        LLMProvider.custom, fresh_settings
    )

    assert models == ["listed-model"]
    assert error is None
    assert seen["api_base"] == "https://inference.example.com/v1"
    assert seen["headers"] == {"x-user": "me@example.com"}


def test_custom_models_without_a_url_say_so(fresh_settings):
    from gitpilot.model_catalog import list_models_for_provider

    models, error = list_models_for_provider(LLMProvider.custom, fresh_settings)
    assert models == []
    assert "No custom endpoint URL configured" in error


# ── Round-tripping the new provider configs ───────────────────────────────


def test_update_settings_merges_custom_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_mod, "_settings", AppSettings())

    settings_mod.update_settings(
        {
            "custom": {
                "base_url": "https://inference.example.com/v1",
                "model": "model-large",
                "headers": {"x-user": "me@example.com"},
            }
        }
    )
    # A later partial update must not wipe unrelated fields.
    updated = settings_mod.update_settings({"custom": {"model": "model-small"}})

    assert updated.custom.base_url == "https://inference.example.com/v1"
    assert updated.custom.model == "model-small"
    assert updated.custom.headers == {"x-user": "me@example.com"}


def test_provider_summary_covers_every_provider(fresh_settings):
    """Every provider must produce a summary, not fall through to the else."""
    for provider in LLMProvider:
        fresh_settings.provider = provider
        summary = fresh_settings.get_provider_summary()
        assert summary.name.value == provider.value
