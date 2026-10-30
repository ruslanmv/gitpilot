# GitPilot sign-in design handoff

This implementation turns the generated login concept into a working React login experience, not a raster screenshot used as a form.

## Original design concept

![Previous generated GitPilot login reference](assets/gitpilot-login-concept-reference.jpg)

The image above is an optimized review thumbnail of the original generated concept. The full-resolution concept is maintained in the design conversation, while this repository version ensures GitHub can display it in the pull request.

## Production illustration

**[Open vector background artwork](../frontend/public/assets/auth/gitpilot-hero-illustration.svg)**

The production asset is located at:

`frontend/public/assets/auth/gitpilot-hero-illustration.svg`

It is a self-contained 1000 × 655 SVG, composed of a GitPilot code-review dashboard, GitHub and GitLab provider tiles, orbit lines and warm ambient lighting. It does not require image libraries, remote CDNs, fonts or additional build dependencies. The image is deliberately separate from live text and form controls, so they stay selectable and accessible. The SVG has a title/description for standalone opening; the in-page instance is decorative (`alt=""`, `aria-hidden="true"`).

In Vite, the browser resolves it at `/assets/auth/gitpilot-hero-illustration.svg`.

## Implemented UI pieces

| Component | Where | Behavior |
| --- | --- | --- |
| Brand icon and GitPilot wordmark | `AuthAside` in `AuthPage.jsx` | Home navigation |
| Marketing headline and explanatory copy | `AuthAside` | HTML text, not embedded into the illustration |
| Four benefit items | `FeatureIcon` + `gp-auth-features` (a list) | SVG icons and responsive layout |
| Left visual background | `gp-auth-illustration` | Local vector asset |
| GitHub button | `AuthPage` | GitHub OAuth (state + S256 PKCE, exchanged server-side); device flow when the server has no client secret |
| GitLab button | `AuthPage` | GitLab.com or the configured self-managed instance, OAuth with state + S256 PKCE |
| Provider availability | `GET /api/auth/providers` | A provider the server has not configured is shown disabled with an explanation, never as a dead button |
| Email and password fields | `AuthPage` | Labels, correct autocomplete, password reveal; hidden when the server has accounts disabled |
| Sign in / sign up / forgot password | `AuthPage` | Account API flows |
| Email confirmation view | `/auth/verify-email?token=` | Confirms, signs in, offers resend on failure |
| Password reset view | `/auth/reset-password?token=` | New + confirm password; the link works once; other sessions are signed out |
| Light/dark appearance toggle | `AppearanceControl` | Live state + persisted browser preference (`gitpilot_auth_theme`) |
| Authorization, device and error views | `AuthPage` | Same responsive card |
| URL → view and OAuth return checks | `frontend/utils/authFlow.js` | Unit-tested (`frontend/tests/authFlow.test.js`) |
| Styles | `frontend/components/auth.css` | Desktop, tablet, mobile and light theme |

The page is wrapped in a `<main>` landmark, the card is labelled by its heading, focus
moves to the heading on every state change, notices are announced (`role="status"` /
`role="alert"`), and busy buttons carry `aria-busy`. The decorative "English" label of
the concept is not rendered: there is no language switcher, and a control-looking label
that does nothing would be a fake control.

## Design tokens

- Background: `#0b0d12`
- Warm accent: `#ff7046` – `#ff5930`
- Default field surface: `#161920`
- Muted text: `#aab1be`
- Accent icons: `#ff814a`
- Card border: `rgba(205,214,232,.15)`

## Responsive layout

- Desktop: marketing/illustration panel on the left, centered authentication card on the right.
- Narrow laptop: hero features become two columns while the illustration remains cropped at the lower edge.
- Mobile/tablet (990px or less): illustration panel hides to prioritize the fully functional, readable sign-in form. The theme control and home link remain accessible.
- Reduced motion: animated affordances follow the user's reduced-motion preference.

## Verification checklist

Checked on the production build (`vite build`, served with `vite preview`):

- [x] Desktop 1440 × 900: two-panel layout, card fits the viewport without scrolling
- [x] Mobile 375 × 812: illustration panel hidden, 12 px gutters, no horizontal scroll
- [x] Focus moves to the card heading on every view; every field has a label
- [x] Reset-password link view renders and removes the token from the address bar
- [x] A callback URL this tab did not start is refused before contacting the server
- [x] Built static assets are served at `/assets/auth/gitpilot-hero-illustration.svg`
- [ ] Real GitHub and GitLab OAuth round trips with configured client credentials (needs real applications; the flows are covered by mocked tests in `tests/test_auth_sessions.py`)
- [ ] Cross-browser pass (Safari, Firefox) and 1672 × 941 / ultrawide visual check

## Security and provider scope

Provider access tokens never reach the browser. OAuth codes are exchanged by the server
with a PKCE verifier it keeps, and the GitHub/GitLab tokens are stored encrypted in the
server-side session vault. The browser holds an HttpOnly session cookie, or a header
token on cross-site deployments where the cookie is blocked. The `state` of an OAuth
return must match the one stored by this tab *and* the single-use one on the server.
GitLab supports the same agent, branch, merge-request and issue workflows as GitHub.
See [auth.md](auth.md) and [GITLAB_INTEGRATION.md](GITLAB_INTEGRATION.md).
