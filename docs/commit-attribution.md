# Make GitPilot's commits appear as "GitPilot" (with an icon)

GitHub picks the avatar on a commit / PR / contributors graph from the commit
**author/committer** or a **`Co-authored-by:`** trailer whose email maps to a
GitHub account. GitPilot uses both:

## 1. GitHub App — the icon (recommended)

GitPilot ships a GitHub App (`GITHUB_APP_SLUG=gitpilota`, `GITHUB_APP_ID=2313985`).
When commits/PRs are created through the **App installation token**, GitHub
attributes them to **`gitpilota[bot]`** and shows the App's avatar automatically.

To get the icon: **GitHub → Settings → Developer settings → GitHub Apps →
GitPilot → Display information → upload a logo.** That logo is the icon you'll see
on every `gitpilota[bot]` commit and PR — the same way Claude Code shows its mark.

Install the App on the target repos and have GitPilot use the installation token
for writes (the API helpers in `github_app.py` / `github_pulls.py` already accept
a token).

## 2. Co-authored-by trailer — credit on human-authored commits

When a human's token authors the commit, GitPilot appends a trailer so it's still
credited as a contributor (this is what Claude Code does):

```text
<your message>

🤖 Generated with GitPilot

Co-authored-by: GitPilot <gitpilota[bot]@users.noreply.github.com>
```

This is applied automatically by `gitpilot.commit_attribution.with_attribution()`
on the file-commit path. Configure:

| Env | Default | Purpose |
|---|---|---|
| `GITPILOT_COMMIT_ATTRIBUTION` | `true` | Toggle the trailer on/off |
| `GITPILOT_BOT_NAME` | `GitPilot` | Display name in the trailer |
| `GITPILOT_BOT_EMAIL` | `gitpilota[bot]@users.noreply.github.com` | Set to a real GitPilot bot account's email so its avatar resolves |

> The avatar next to a co-author only renders if the email maps to a GitHub
> account. Use the App bot's no‑reply email (default) or a dedicated `gitpilot`
> bot account.
