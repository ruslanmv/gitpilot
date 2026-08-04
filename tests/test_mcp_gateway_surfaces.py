"""The gateway is configurable from every surface, and identically.

The desktop UI, the mobile UI and the VS Code extension are three front ends
over one API. If one of them grows its own copy of the settings — or worse, its
own place to store a password — they drift, and a gateway configured in the
editor stops matching the one the agents actually call.

These are structural checks on that arrangement, not on pixels.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "components" / "AdminTabs"
VSCODE = ROOT / "extensions" / "vscode"

GATEWAY_ENDPOINTS = ("/api/mcp/gateway", "/api/mcp/gateway/test")


# ── Desktop + mobile (the same component serves both) ───────────────────────

def test_the_mcp_tab_has_a_gateway_section():
    tab = (FRONTEND / "MCPServersTab.jsx").read_text(encoding="utf-8")
    assert "GatewayForm" in tab
    assert "TAB_GATEWAY" in tab
    # Switching gateway changes which registry you are looking at, so the
    # installed list has to reload with it.
    assert "onSaved={refresh}" in tab


def test_the_gateway_form_asks_for_url_and_credentials():
    form = (FRONTEND / "mcp" / "GatewayForm.jsx").read_text(encoding="utf-8")
    assert "Gateway URL" in form
    assert "Admin email" in form and "Admin password" in form
    assert "API token" in form
    assert "Test connection" in form


def test_the_form_never_renders_a_stored_secret():
    """The API returns booleans, and the UI must treat them as such."""
    form = (FRONTEND / "mcp" / "GatewayForm.jsx").read_text(encoding="utf-8")
    assert "hasPassword" in form and "hasApiToken" in form
    # Password inputs are masked...
    assert form.count('type="password"') >= 2
    # ...and a blank box means "keep it", so the secret is only sent when typed.
    assert "if (draft.password) body.password" in form
    assert "if (draft.apiToken) body.apiToken" in form
    # Removing a credential is a deliberate action, not an empty field.
    assert "clearPassword" in form and "clearApiToken" in form


def test_the_form_is_usable_on_a_phone():
    """One component for desktop and mobile: it has to reflow, not scroll sideways."""
    form = (FRONTEND / "mcp" / "GatewayForm.jsx").read_text(encoding="utf-8")
    assert "minmax(220px, 1fr)" in form  # credential row collapses to one column
    assert "boxSizing: \"border-box\"" in form  # inputs never overflow their container
    assert "minHeight: \"40px\"" in form  # touch target


def test_every_field_is_labelled():
    """Screen readers and voice control need real labels, not placeholders."""
    form = (FRONTEND / "mcp" / "GatewayForm.jsx").read_text(encoding="utf-8")
    for field in ("mcp-gateway-url", "mcp-gateway-auth", "mcp-gateway-email",
                  "mcp-gateway-password", "mcp-gateway-token"):
        assert f'htmlFor="{field}"' in form, f"{field} has no label"
        assert f'id="{field}"' in form, f"{field} has no id"
    assert 'role="alert"' in form and 'role="status"' in form


# ── VS Code ─────────────────────────────────────────────────────────────────

def test_the_extension_contributes_the_gateway_commands():
    manifest = json.loads((VSCODE / "package.json").read_text(encoding="utf-8"))
    commands = {c["command"] for c in manifest["contributes"]["commands"]}
    assert {
        "gitpilot.configureMcpGateway",
        "gitpilot.testMcpGateway",
        "gitpilot.syncMcpServers",
        "gitpilot.signOutMcpGateway",
    } <= commands


def test_no_credential_is_ever_a_vs_code_setting():
    """Settings sync across machines and can be committed. Secrets cannot live there."""
    manifest = json.loads((VSCODE / "package.json").read_text(encoding="utf-8"))
    properties = manifest["contributes"]["configuration"]["properties"]
    mcp_keys = [k for k in properties if k.startswith("gitpilot.mcp.")]

    assert "gitpilot.mcp.gatewayUrl" in mcp_keys
    assert "gitpilot.mcp.authMode" in mcp_keys
    assert "gitpilot.mcp.adminEmail" in mcp_keys
    for key in mcp_keys:
        assert "password" not in key.lower(), f"{key} would store a password in settings.json"
        assert "token" not in key.lower(), f"{key} would store a token in settings.json"

    # And the email setting says where the password does go.
    assert "password is NOT a setting" in properties["gitpilot.mcp.adminEmail"]["description"]


def test_the_extension_prompts_for_secrets_and_sends_them_to_the_server():
    source = (VSCODE / "src" / "commands" / "mcpGatewayCommands.ts").read_text(encoding="utf-8")
    # Masked prompts, not settings writes.
    assert source.count("password: true") >= 2
    # Only the non-secret half is mirrored into settings.
    assert 'settings().update("gatewayUrl"' in source
    assert 'settings().update("adminEmail"' in source
    assert 'settings().update("password' not in source
    assert 'settings().update("apiToken' not in source
    # Proved before saved.
    assert "gatewayClient.test(update)" in source
    assert source.index("gatewayClient.test(update)") < source.index("gatewayClient.save(update)")


def test_all_three_surfaces_call_the_same_endpoints():
    """One authority. A surface with its own storage is a surface that drifts."""
    web = (FRONTEND / "mcp" / "GatewayForm.jsx").read_text(encoding="utf-8")
    editor = (VSCODE / "src" / "api" / "mcpGatewayClient.ts").read_text(encoding="utf-8")
    for endpoint in GATEWAY_ENDPOINTS:
        assert endpoint in web, f"the web UI does not use {endpoint}"
        assert endpoint in editor, f"the extension does not use {endpoint}"


# ── Starting the dev server on any filesystem ───────────────────────────────

def test_the_frontend_launcher_survives_a_missing_exec_bit():
    """On a Windows checkout used from WSL (/mnt/c), node_modules/.bin/vite has
    no exec bit and `npm run dev` dies with "vite: Permission denied". The bit
    belongs to the mount, not the project, so reinstalling does not fix it —
    handing Vite's entry script to node does.
    """
    script = (ROOT / "scripts" / "run-frontend.sh").read_text(encoding="utf-8")
    # The healthy path is unchanged.
    assert 'if [[ -x "${VITE_BIN}" ]]' in script
    assert "exec npm run dev" in script
    # ...and the fallback needs no exec bit at all.
    assert 'exec node "${VITE_ENTRY}"' in script
    assert "node_modules/vite/bin/vite.js" in script
    # Missing dependencies get a next step, not a stack trace.
    assert "make frontend-install" in script


def test_make_run_uses_the_launcher_rather_than_npm_directly():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "scripts/run-frontend.sh" in makefile
    # No raw `npm run dev` left to hit the same wall.
    assert "npm run dev -- --open" not in makefile
