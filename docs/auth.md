# GitPilot accounts — premium, account‑first authentication

Today GitPilot is **bring‑your‑own‑GitHub‑token** (the GitHub token *is* the
identity, stored in `localStorage`). That's correct for a single local user, but
not for a multi‑user product. This is the design for a **GitPilot account first,
GitHub second** model.

```text
GitPilot account   = who the user is        (email / Google / GitHub login)
GitHub connection  = which repos they access (connected after sign‑in)
Resend             = email delivery only     (verification + reset)
```

> **Do not** make GitHub device authorization the only login.

## The flow

```text
Landing page
  ↓  Get started / Sign in
Create GitPilot account  (email+password · or Continue with GitHub/Google)
  ↓
Verify email (link sent via Resend)
  ↓
Dashboard
  ↓  Connect GitHub  (GitHub App install / OAuth — stored under the account)
Select repository
  ↓
Run GitPilot workflow
```

Routes (frontend): `/` · `/auth` · `/auth/verify-email` · `/auth/forgot-password`
· `/auth/reset-password` · `/dashboard` · `/onboarding/connect-github` ·
`/account` · `/account/security` · `/logout`.

## API (implemented: `gitpilot/auth/`, mount with `GITPILOT_ENABLE_ACCOUNTS=true`)

| Endpoint | Behaviour |
|---|---|
| `POST /api/account/signup` | Create a *pending* account, email a verification link. **202 + generic message** always (no enumeration). |
| `POST /api/account/verify-email` | Consume the link token → mark verified → set session cookie. |
| `POST /api/account/login` | Verify password; **generic 401** "Invalid email or password"; 403 if unverified. Sets session cookie. |
| `POST /api/account/logout` | Clears the session cookie. |
| `GET  /api/account/me` | The signed‑in account, or 401. |
| `POST /api/account/password/forgot` | **Always 200** "If an account exists…" (no enumeration); emails a reset link if it does. |
| `POST /api/account/password/reset` | Consume the reset token → set new password (proves ownership → verified) → session. |

## Security (OWASP Authentication Cheat Sheet)

- **Password hashing:** PBKDF2‑HMAC‑SHA256, **600,000 iterations**, per‑password
  salt, constant‑time compare. Never store plaintext (`gitpilot/auth/security.py`).
- **No account enumeration:** signup, login, and forgot‑password return identical
  generic responses whether or not the email exists.
- **Email verified before use:** the account can't sign in until the link is clicked.
- **Signed, short‑lived links:** HMAC‑SHA256, purpose‑scoped (`verify-email` vs
  `reset-password`), 15‑minute expiry; a reset token can't activate and vice‑versa.
- **Sessions are HttpOnly cookies**, not `localStorage`: signed, `Secure` (on https),
  `SameSite=Lax`, 7‑day expiry. **Logout clears the cookie** server‑side — it is not
  just a client‑side delete.
- **GitHub is a connected provider, not the identity:** the existing `/api/auth/*`
  GitHub OAuth / device / App flow is used **after** login to grant *repository
  access*, stored under the account — not as the login itself.

## Configuration

| Env | Purpose |
|---|---|
| `GITPILOT_ENABLE_ACCOUNTS` | Mount the accounts API (default off) |
| `GITPILOT_AUTH_SECRET` | **Required in prod** — HMAC key for tokens/sessions |
| `RESEND_API_KEY` | Resend API key (no key ⇒ dev no‑op that logs the link) |
| `GITPILOT_EMAIL_FROM` | e.g. `GitPilot <noreply@gitpilot.ruslanmv.com>` |
| `GITPILOT_PUBLIC_BASE_URL` | Base URL for the links in emails |

## Multi‑tenant data model (the durable fix)

Every record is keyed by `owner_id` (= the account id) — **no shared/global
record**. The implemented store is JSON‑file backed (single node); the same
interface maps to per‑user database tables for SaaS:

```text
users(id, email, name, created_at)
auth_accounts(user_id, password_hash, email_verified_at, provider)
sessions(id, user_id, expires_at)            # or stateless signed cookie
email_verification_tokens / password_reset_tokens   # or stateless signed links
github_connections(user_id, installation_id, login)
github_repositories(user_id, repo_full_name, access)
audit_events(user_id, type, created_at)
```

This is what closes the multi‑user gaps in the current GitPilot (global settings
file, shared sessions/workspaces, shared LLM keys): with accounts, every user's
config, GitHub connection, runs, and workspaces are scoped to their `user_id`.

## Status

Implemented and tested (`tests/test_accounts.py`): email/password signup →
verification → login → session → forgot/reset, with the anti‑enumeration and
hashing guarantees. **Next steps:** OAuth "Continue with GitHub/Google" wired to
the same account, a real per‑user DB (replace the JSON store), the
`/onboarding/connect-github` step, and the React auth pages.
