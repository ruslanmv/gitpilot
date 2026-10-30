# Authentication, sessions and provider connections

GitPilot signs people in with **GitHub**, **GitLab** or an optional **email/password
account**, and keeps every provider credential on the server.

```text
GitPilot web session  = this browser, signed in            (HttpOnly cookie)
GitPilot account      = who the user is (optional)          (email + password)
GitHub connection     = GitHub repositories it may use      (OAuth token, server-side)
GitLab connection     = GitLab projects it may use          (OAuth token, server-side)
```

A session can hold an account, a GitHub connection, a GitLab connection, or any
combination. Signing in with GitLab after GitHub (or the reverse) adds the second
connection to the same session.

## What the browser holds

| Held by the browser | Not held by the browser |
|---|---|
| An opaque session id in the `gitpilot_session` **HttpOnly** cookie | GitHub / GitLab access tokens |
| The session's CSRF token, **in memory only** | Refresh tokens |
| A non-secret `gitpilot_signed_in` hint (so a reload shows the loader, not the landing page) | PKCE verifiers |

When the SPA and the API are on different sites (for example a Vercel frontend and
a Hugging Face backend) and the browser blocks the cross-site cookie, the SPA
detects that right after sign-in and sends the same session id in the
`X-GitPilot-Session` header instead, keeping it in `localStorage`. Provider tokens
stay server-side either way.

## Sign-in flows

### GitHub and GitLab (OAuth 2.0 authorization code + PKCE)

```text
SPA  GET /api/auth/oauth/{provider}/start            → {authorization_url, state}
     (stores {provider, state} in sessionStorage for this tab)
Browser → github.com / your GitLab → back to <frontend>/auth?code=…&state=…
SPA  checks state == the one this tab stored (and < 10 min old)
SPA  POST /api/auth/oauth/{provider}/callback {code, state}
API  consumes the state (single use, 10-minute TTL, same provider, same session)
     exchanges the code with client_secret + PKCE code_verifier
     stores the tokens encrypted on the session, rotates the session id
     → {providers, csrf_token, session_token}   (no access token)
```

- `state` is 256 bits of randomness; only its SHA-256 is stored. It is bound to the
  session that started the flow, so a callback replayed into another browser session
  is refused (login CSRF), and it can be used once.
- PKCE uses `S256` for both providers. The verifier is generated and kept by the
  server.
- The session id is **rotated** on every sign-in or provider connection (session
  fixation defence).
- `GET /api/auth/url` and `POST /api/auth/callback` (GitHub) and
  `/api/gitlab/auth/url`, `/api/gitlab/auth/callback` (GitLab) remain as aliases of
  the same flow. A non-browser client may pass `"return_token": true` to the GitHub
  callback to receive the token.

### GitHub device flow

When the server has no `GITHUB_CLIENT_SECRET`, "Continue with GitHub" uses the
device flow (`/api/auth/device/code`, `/api/auth/device/poll`). The web app polls
with `{"store": true}`, so the token is stored on the session and not returned.
CLI and VS Code clients poll without it and receive the token as before.

### Email and password (optional)

Mounted with `GITPILOT_ENABLE_ACCOUNTS=true`. The sign-in page hides the email form
when accounts are disabled (it reads `GET /api/auth/providers`).

| Endpoint | Behaviour |
|---|---|
| `POST /api/account/signup` | Creates a pending account and emails a verification link. Always **202** with a generic message (no account enumeration). |
| `POST /api/account/verify-email` | Consumes the link, marks the account verified, starts a session. |
| `POST /api/account/login` | **Generic 401** on a wrong email or password; 403 if unverified. Starts (or upgrades) a session. |
| `POST /api/account/logout` | **Revokes** the server-side session and clears the cookie. |
| `GET  /api/account/me` | The signed-in account, or 401. |
| `POST /api/account/password/forgot` | Always 200 with a generic message; emails a reset link if the account exists. |
| `POST /api/account/password/reset` | Sets the new password, **signs out every session** of the account, and starts a new one. A reset link stops working once the password has changed. |
| `POST /api/account/password/change` | Re-issues this session and signs out every other session. |

The links in emails point at `<GITPILOT_PUBLIC_BASE_URL>/auth/verify-email?token=…`
and `/auth/reset-password?token=…`; both views are part of the sign-in page.

## Session API

| Endpoint | Purpose |
|---|---|
| `GET  /api/auth/session` | `{authenticated, account, providers{github,gitlab}, csrf_token, expires_at}`. Provider entries carry the identity and expiry, never a token. |
| `GET  /api/auth/providers` | Which sign-in methods this server has configured. |
| `POST /api/auth/session/import` | One-time migration of a token an earlier version kept in `localStorage` (see below). |
| `POST /api/auth/providers/{provider}/disconnect` | Revokes the token at the provider and removes it from the session. |
| `POST /api/auth/logout` | Revokes every provider token of the session at the provider and deletes the session. |
| `POST /api/auth/ws-ticket` | A single-use, 60-second ticket for opening a WebSocket (`?ticket=`). |

## Request authentication

`WebSessionMiddleware` (`gitpilot/auth/middleware.py`) runs on every HTTP request and
WebSocket upgrade:

1. It resolves the session from `X-GitPilot-Session`, the `gitpilot_session` cookie,
   or a WebSocket `?ticket=`.
2. **CSRF.** A state-changing `/api/` request whose session came from the cookie must
   either send the session's token in `X-GitPilot-CSRF` (the SPA does), or come from a
   trusted origin. A trusted origin is judged by `Origin`, or by `Referer` when `Origin`
   is absent, and is either the API's own origin or one listed in `CORS_ORIGINS` /
   `CORS_ORIGIN_REGEX`. A `*` never counts as trusted. Anything else gets 403.
   Requests authenticated by header are not exposed to CSRF. Login, signup and
   email-link endpoints are exempt. WebSocket upgrades from an untrusted `Origin`
   never use the cookie, which prevents cross-site WebSocket hijacking.
3. **Refresh.** Provider tokens that expire within two minutes are refreshed with
   their refresh token before the request runs. GitLab tokens live two hours; GitHub
   App user tokens live eight hours. A token that cannot be refreshed is dropped, and
   the UI asks the user to reconnect.
4. It binds the session for the request.

Routes then pick the credential **for the provider of the repository they act on**,
in this order: an explicit `Authorization` header from an API client, then the
session's connection for that provider, then (single-user installs only) the server's
`GITPILOT_GITHUB_TOKEN` / `GITPILOT_GITLAB_TOKEN`. Two guards stop a token from ever
reaching the other provider:

- A header token is ignored when the client declared the other provider
  (`X-GitPilot-Git-Provider`).
- A token with the other provider's visible prefix (`gh*_`, `github_pat_`, `glpat-`, …)
  is refused.

Inside a GitLab request scope, the GitHub helpers do not run at all, and the reverse
also holds (see [GITLAB_INTEGRATION.md](GITLAB_INTEGRATION.md#provider-isolation)).

## Storage and encryption

- Sessions, credentials, pending OAuth states and WebSocket tickets are kept in
  SQLite (WAL mode) at `<GITPILOT_CONFIG_DIR>/auth/web_sessions.db`, or at
  `GITPILOT_AUTH_DB`. Every worker on the host shares it.
- Rows are keyed by the **SHA-256** of the session id. A copy of the database does
  not contain usable session ids.
- Access and refresh tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
  The key comes from `GITPILOT_TOKEN_ENCRYPTION_KEY`, which takes one or more keys
  separated by commas: the first encrypts, and all of them decrypt (key rotation).
  If that is not set, the key is derived from `GITPILOT_AUTH_SECRET` with HKDF. If
  neither is set, a key file `auth/vault.key` (mode 0600) is generated for
  single-machine use and a warning is logged.
- Sessions expire after `GITPILOT_SESSION_TTL_HOURS` of inactivity (default 168,
  sliding) and are revoked on logout.

## Cookies and CORS

| Setting | Default | Notes |
|---|---|---|
| `GITPILOT_COOKIE_SAMESITE` | `lax` | `none` is honoured only when `CORS_ORIGINS` is an explicit allowlist, and always with `Secure`. |
| `GITPILOT_COOKIE_SECURE` | auto | `Secure` when the request is https (including behind a TLS proxy) or `GITPILOT_PUBLIC_BASE_URL` is https. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated exact origins. `*` still works for compatibility, but cookies then stay `SameSite=Lax` and a warning is logged. |
| `CORS_ORIGIN_REGEX` | — | For preview deployments, e.g. `https://gitpilot-[a-z0-9-]+\.vercel\.app`. |

## Migrating existing users

Earlier versions stored `github_token` / `gitlab_token` in `localStorage`. On the
first load after the upgrade, the SPA:

1. sends each stored token once to `POST /api/auth/session/import`. The server
   validates it with the provider and stores it encrypted.
2. deletes `github_token`, `github_user`, `gitlab_token`, `gitlab_user` and
   `gitpilot_git_provider` from the browser, whether or not the import succeeded.

Account sessions signed by earlier versions (stateless HMAC tokens) are still accepted
until they expire. `GET /api/auth/session` exchanges such a token for a server-side
session. Pending OAuth flows started before the upgrade must be restarted.

No server-side migration is needed: the session database is created on first use.

## Configuration summary

| Variable | Purpose |
|---|---|
| `GITPILOT_PUBLIC_BASE_URL` | Public frontend URL. The default OAuth redirect is `<base>/auth`, and email links use it too. |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | GitHub OAuth App or GitHub App (web flow needs the secret; the device flow needs only the id). |
| `GITHUB_REDIRECT_URI` | Overrides `<base>/auth`. |
| `GITHUB_OAUTH_SCOPES` | Default `repo user:email`. |
| `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_REDIRECT_URI` | GitLab OAuth application. |
| `GITLAB_BASE_URL` | Default `https://gitlab.com`; set to one self-managed instance. |
| `GITLAB_OAUTH_SCOPES` | Default `api read_user`. |
| `GITPILOT_TOKEN_ENCRYPTION_KEY` | Fernet key(s) for the token vault. **Set in production.** |
| `GITPILOT_AUTH_SECRET` | HMAC key for email links (and the vault key fallback). **Set in production.** |
| `GITPILOT_AUTH_DB` | Session database path (shared volume for several hosts). |
| `GITPILOT_SESSION_TTL_HOURS` | Idle session lifetime. |
| `GITPILOT_ENABLE_ACCOUNTS`, `RESEND_API_KEY`, `GITPILOT_EMAIL_FROM` | Email/password accounts and email delivery. |

Generate a vault key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Password handling

- PBKDF2-HMAC-SHA256 with 600,000 iterations, a per-password salt and constant-time
  comparison.
- Signup, login and forgot-password give generic answers, so they do not reveal
  whether an email has an account.
- Email links are HMAC-signed, purpose-scoped and expire after 15 minutes.

## Tests

- `tests/test_auth_sessions.py` covers:
  - OAuth start (state, S256 PKCE, redirect) for both providers;
  - callbacks with an invalid, expired, reused or wrong-provider state, and a state from another session;
  - encrypted storage, session rotation, header versus cookie transport and CSRF enforcement;
  - token refresh, and dropping a token that cannot be refreshed;
  - logout and disconnect with revocation, legacy-token import, WebSocket tickets;
  - account sessions, single-use reset links, and migration of legacy account tokens.
- `tests/test_accounts.py` covers the account flows.
- `frontend/tests/authSession.test.js` and `authFlow.test.js` cover the browser side.
