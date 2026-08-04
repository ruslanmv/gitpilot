"""Finding a Forge, and authenticating with it.

Two behaviours worth protecting, both of which were previously guessed at and
both of which failed silently when the guess was wrong:

* A Forge that is already running (HomePilot's, typically) is reused, and never
  stopped by us. Two gateways on one machine split the tool registry in half.
* The credential is whatever *that* Forge accepts. With AUTH_REQUIRED=false the
  correct request carries no Authorization header at all — Forge's anonymous
  path is only reached when the header is absent, so a placeholder token is
  worse than none.

The library is bash, so these tests drive it through bash with a stub `curl` and
`docker` on PATH. That keeps the assertions on the decisions, not on plumbing.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "forge-auth.sh"


@pytest.fixture()
def forge(tmp_path):
    """Run snippets against the library with a scripted fake Forge.

    `routes` maps a URL substring to "STATUS<TAB>BODY"; the stub curl replies
    with the first match, so a test describes a Forge by what it answers.
    """

    def _run(snippet: str, routes: dict[str, str], env: dict | None = None,
             forge_container_running: bool = False) -> subprocess.CompletedProcess:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)

        routes_file = tmp_path / "routes"
        routes_file.write_text(
            "\n".join(f"{k}\t{v}" for k, v in routes.items()) + "\n", encoding="utf-8"
        )

        # Stub curl: matches the request URL against the route table, honours
        # -w '%{http_code}' and -o -, and records every invocation so a test can
        # assert on the headers that were (or were not) sent.
        (bin_dir / "curl").write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> "{tmp_path}/curl.log"
            url=""
            want_body=0
            for arg in "$@"; do
              case "$arg" in
                http*) url="$arg" ;;
                -) want_body=1 ;;
              esac
            done
            status=404; body='{{}}'
            # A protected endpoint: 200 only for the one token that works, so a
            # test can tell "configured" apart from "actually accepted".
            if [[ -n "${{STUB_VALID_TOKEN:-}}" && "$url" == *"/gateways"* ]]; then
              if [[ "$*" == *"Authorization: Bearer ${{STUB_VALID_TOKEN}}"* ]]; then
                status=200; body='[]'
              else
                status=401; body='{{"detail":"Invalid authentication credentials"}}'
              fi
              for arg in "$@"; do
                if [[ "$arg" == *"%{{http_code}}"* ]]; then
                  if [[ "$arg" == *'\n'* ]]; then printf '%s\n%s' "$body" "$status"
                  else printf '%s' "$status"; fi
                  exit 0
                fi
              done
              printf '%s' "$body"; exit 0
            fi
            while IFS=$'\\t' read -r pattern reply; do
              [[ -z "$pattern" ]] && continue
              if [[ "$url" == *"$pattern"* ]]; then
                status="${{reply%%|*}}"; body="${{reply#*|}}"; break
              fi
            done < "{routes_file}"
            for arg in "$@"; do
              if [[ "$arg" == *"%{{http_code}}"* ]]; then
                if [[ "$arg" == *'\\n'* ]]; then printf '%s\\n%s' "$body" "$status"
                else printf '%s' "$status"; fi
                exit 0
              fi
            done
            printf '%s' "$body"
            """), encoding="utf-8")
        (bin_dir / "curl").chmod(0o755)

        running = "gitpilot-mcp-context-forge" if forge_container_running else ""
        (bin_dir / "docker").write_text(
            f"#!/usr/bin/env bash\nprintf '%s\\n' '{running}'\n", encoding="utf-8"
        )
        (bin_dir / "docker").chmod(0o755)

        script = tmp_path / "case.sh"
        script.write_text(f"set -uo pipefail\nsource {LIB}\n{snippet}\n", encoding="utf-8")
        return subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, cwd=tmp_path,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                 "FORGE_TOKEN_CACHE": str(tmp_path / ".mcp/forge-token"), **(env or {})},
        )

    return _run


HEALTHY = {"/health": "200|{}"}


# ── Which Forge ─────────────────────────────────────────────────────────────

def test_a_forge_started_elsewhere_is_adopted_not_duplicated(forge):
    """HomePilot's gateway is reused; ours is not started alongside it."""
    result = forge('forge_discover && echo "$FORGE_OWNERSHIP $FORGE_URL"', HEALTHY,
                   forge_container_running=False)
    assert result.stdout.strip() == "external http://localhost:4444"


def test_our_own_forge_is_recognised_as_ours(forge):
    result = forge('forge_discover && echo "$FORGE_OWNERSHIP"', HEALTHY,
                   forge_container_running=True)
    assert result.stdout.strip() == "gitpilot"


def test_no_forge_anywhere_is_reported_not_invented(forge):
    result = forge('forge_discover || echo "none:$FORGE_OWNERSHIP"', {"/health": "000|"})
    assert result.stdout.strip() == "none:none"


def test_an_explicit_url_wins_over_discovery(forge):
    result = forge('forge_discover && echo "$FORGE_URL"',
                   {"forge.internal": "200|{}", "localhost": "200|{}"},
                   env={"MCP_FORGE_URL": "http://forge.internal:4444"})
    assert result.stdout.strip() == "http://forge.internal:4444"


def test_an_adopted_forge_gets_a_host_reachable_address(forge):
    """Service names do not resolve from a gateway on another network.

    Registering a URL Forge cannot dial succeeds and then fails forever, so the
    advertised host changes with ownership.
    """
    adopted = forge('forge_discover; forge_advertise_host', HEALTHY, forge_container_running=False)
    assert adopted.stdout.strip() == "host.docker.internal"

    ours = forge('forge_discover; echo "[$(forge_advertise_host)]"', HEALTHY,
                 forge_container_running=True)
    assert ours.stdout.strip() == "[]"  # same network: service names are correct

    pinned = forge('forge_discover; forge_advertise_host', HEALTHY,
                   env={"MCP_ADVERTISE_HOST": "192.168.1.50"})
    assert pinned.stdout.strip() == "192.168.1.50"


# ── Which credential ────────────────────────────────────────────────────────

def test_auth_disabled_means_no_header_at_all(forge, tmp_path):
    """The bug that started this: a placeholder token is worse than none.

    Forge's anonymous path is only reached when the Authorization header is
    absent, so with auth off we must send nothing.
    """
    result = forge(
        'forge_discover; forge_resolve_auth && echo "mode=$FORGE_AUTH_MODE token=[$FORGE_TOKEN]"',
        {**HEALTHY, "/gateways": "200|[]"},
        env={"MCP_AUTH_TOKEN": "not-a-jwt-just-a-signing-secret"},
    )
    assert result.stdout.strip() == "mode=anonymous token=[]"
    # And the signing secret never appears in any request.
    log = (tmp_path / "curl.log").read_text()
    assert "not-a-jwt-just-a-signing-secret" not in log
    assert "Authorization" not in log


def test_an_operator_supplied_token_is_used_when_it_works(forge):
    """The production path: a revocable API token, no password on disk."""
    result = forge('forge_discover; forge_resolve_auth && echo "[$FORGE_TOKEN]"',
                   HEALTHY,
                   env={"MCP_FORGE_API_TOKEN": "real-token",
                        "STUB_VALID_TOKEN": "real-token"})
    assert result.stdout.strip() == "[real-token]"


def test_a_token_that_forge_rejects_is_not_used(forge):
    """The original bug: the JWT signing secret sent as a bearer token.

    It must not be handed to Forge on the strength of being configured — only
    a token Forge actually accepts is used.
    """
    result = forge('forge_discover; forge_resolve_auth || echo "refused"',
                   HEALTHY,
                   env={"MCP_FORGE_API_TOKEN": "the-signing-secret",
                        "STUB_VALID_TOKEN": "a-different-real-token"})
    assert result.stdout.strip() == "refused"


def test_a_password_is_exchanged_for_a_token_and_cached(forge, tmp_path):
    """The credential on disk is a password; what we send is a short-lived JWT."""
    result = forge(
        'forge_discover; forge_resolve_auth && echo "[$FORGE_TOKEN]"',
        {"/health": "200|{}",
         "/auth/email/login": '200|{"access_token": "jwt-abc", "token_type": "bearer"}'},
        env={"MCP_FORGE_ADMIN_PASSWORD": "s3cret", "STUB_VALID_TOKEN": "jwt-abc"},
    )
    assert result.stdout.strip() == "[jwt-abc]"
    # Cached, so the next run does not spend the password again.
    assert (tmp_path / ".mcp/forge-token").read_text().strip() == "jwt-abc"


def test_a_cached_token_is_reused_without_logging_in_again(forge, tmp_path):
    cache = tmp_path / ".mcp/forge-token"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("jwt-cached\n")

    result = forge('forge_discover; forge_resolve_auth && echo "[$FORGE_TOKEN]"',
                   HEALTHY, env={"STUB_VALID_TOKEN": "jwt-cached"})
    assert result.stdout.strip() == "[jwt-cached]"
    assert "/auth/email/login" not in (tmp_path / "curl.log").read_text()


def test_a_stale_cached_token_falls_through_to_a_fresh_login(forge, tmp_path):
    """A cache that has expired must not become a permanent failure."""
    cache = tmp_path / ".mcp/forge-token"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("jwt-expired\n")

    result = forge(
        'forge_discover; forge_resolve_auth && echo "[$FORGE_TOKEN]"',
        {"/health": "200|{}", "/auth/email/login": '200|{"access_token": "jwt-fresh"}'},
        env={"MCP_FORGE_ADMIN_PASSWORD": "s3cret", "STUB_VALID_TOKEN": "jwt-fresh"},
    )
    assert result.stdout.strip() == "[jwt-fresh]"
    assert cache.read_text().strip() == "jwt-fresh"


def test_login_exchanges_the_password_for_a_jwt(forge):
    result = forge('forge_login http://localhost:4444',
                   {"/auth/email/login": '200|{"access_token": "jwt-abc"}'},
                   env={"MCP_FORGE_ADMIN_PASSWORD": "s3cret"})
    assert result.stdout.strip() == "jwt-abc"


def test_login_without_a_password_does_not_pretend_to_succeed(forge):
    result = forge('forge_login http://localhost:4444 || echo "no-credential"',
                   {"/auth/email/login": '200|{"access_token": "jwt-abc"}'},
                   env={"MCP_FORGE_ADMIN_PASSWORD": ""})
    assert result.stdout.strip() == "no-credential"


def test_a_protected_forge_with_no_usable_credential_fails_loudly(forge):
    result = forge(
        'forge_discover; forge_resolve_auth || forge_explain_auth_failure',
        {**HEALTHY, "/gateways": "401|{}", "/auth/email/login": "401|{}"},
        env={"MCP_FORGE_ADMIN_PASSWORD": "wrong"},
    )
    assert "requires a credential" in result.stdout
    # The message names the fix, including the trap that caused the original bug.
    assert "MCP_FORGE_API_TOKEN" in result.stdout
    assert "signing secret is NOT a bearer token" in result.stdout


def test_the_auth_header_is_only_built_when_there_is_a_token(forge):
    with_token = forge('FORGE_TOKEN=abc; forge_auth_args', {})
    assert with_token.stdout.strip().splitlines() == ["-H", "Authorization: Bearer abc"]

    without = forge('FORGE_TOKEN=""; forge_auth_args; echo "end"', {})
    assert without.stdout.strip() == "end"


def test_a_cached_token_is_owner_only(forge, tmp_path):
    forge('_forge_cache_token "jwt-xyz"', {})
    cache = tmp_path / ".mcp/forge-token"
    assert cache.read_text().strip() == "jwt-xyz"
    assert oct(cache.stat().st_mode)[-3:] == "600"


# ── Reaching the servers we ask Forge to register ───────────────────────────

def test_an_unreachable_url_reports_exactly_000(forge):
    """The bug this pins: curl prints 000 itself on a connection failure, so an
    `|| echo 000` fallback concatenated into "000000" — which is not equal to
    "000" and silently defeated every "is it down?" check."""
    result = forge('echo "[$(forge_status http://127.0.0.1:9/health)]"', {"/health": "000|"})
    assert result.stdout.strip() == "[000]"


def test_the_mcp_endpoint_is_discovered_not_assumed(forge):
    """Forge handshakes at registration, so a wrong path is a 503 later.

    Asking the server which path it answers on turns that into a correct
    registration instead of an opaque failure.
    """
    streamable = forge('mcp_discover_endpoint http://server.test',
                       {"/mcp": "200|{}"})
    assert streamable.stdout.strip() == "/mcp STREAMABLEHTTP"

    # A server that only speaks SSE must be registered as SSE.
    sse = forge('mcp_discover_endpoint http://server.test',
                {"/mcp": "404|{}", "/mcp/": "404|{}", "/sse": "200|{}"})
    assert sse.stdout.strip() == "/sse SSE"


def test_a_server_speaking_no_mcp_path_is_reported_not_guessed(forge):
    result = forge('mcp_discover_endpoint http://server.test || echo "none"',
                   {"/": "404|{}"})
    assert result.stdout.strip() == "none"


def test_an_endpoint_that_dislikes_the_probe_still_counts(forge):
    """400/406/415 mean "wrong request shape", not "wrong path" — Forge's own
    client will handshake properly, so the path is right."""
    for code in ("400", "406", "415"):
        result = forge('mcp_discover_endpoint http://server.test', {"/mcp": f"{code}|{{}}"})
        assert result.stdout.strip() == "/mcp STREAMABLEHTTP", f"HTTP {code} was discarded"


def test_waiting_for_a_server_gives_up_rather_than_hanging(forge):
    result = forge('mcp_wait_for_server http://server.test 3 || echo "timed-out"',
                   {"/health": "000|"})
    assert result.stdout.strip() == "timed-out"


def test_any_http_answer_means_the_server_is_up(forge):
    """/health may legitimately 404; what matters is that something answered."""
    result = forge('mcp_wait_for_server http://server.test 3 && echo "up"',
                   {"/health": "404|{}"})
    assert result.stdout.strip() == "up"
