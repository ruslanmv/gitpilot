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
| Four benefit items | `FeatureIcon` + `gp-auth-features` | SVG icons and responsive layout |
| Left visual background | `gp-auth-illustration` | Local vector asset |
| GitHub button | `AuthPage` | Existing GitHub web/device OAuth |
| GitLab button | `AuthPage` | GitLab OAuth code + state + PKCE |
| Email and password fields | `AuthPage` | Labels, correct autocomplete, password reveal |
| Sign in / sign up / reset | `AuthPage` | Existing account API flows retained |
| Light/dark appearance toggle | `AppearanceControl` | Live state + persisted browser preference |
| Authorization, device and email verification views | `AuthPage` | Same responsive visual shell |
| Styles | `frontend/components/auth.css` | Desktop, tablet, mobile and light theme |

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

- [ ] Desktop visual check at 1440 × 900, 1672 × 941 and ultrawide sizes
- [ ] Mobile checks at 375px, 390px and 768px
- [ ] Keyboard-only tab order, field focus, form errors and password reveal
- [ ] Dark/light toggle survives refresh
- [ ] Existing GitHub OAuth web + device flow succeeds
- [ ] GitLab OAuth code callback succeeds with configured environment
- [ ] Account sign-up, email verification and password recovery
- [ ] Ensure built static assets are served at `/assets/auth/gitpilot-hero-illustration.svg`

## Security and provider scope

The visual update does not alter GitHub credentials or change authorization logic. GitLab project browsing is the first integration stage; GitLab agent execution and merge requests remain disabled until provider-aware agents exist. For enterprise production, server-side encrypted token storage and shared OAuth state storage should replace browser localStorage and in-memory process state across both providers.
