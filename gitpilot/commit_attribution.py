"""GitPilot commit attribution — make GitPilot's commits show up as "GitPilot".

GitHub decides the avatar in the contributors graph and on commits/PRs from the
commit author/committer, or from a ``Co-authored-by:`` trailer whose email maps
to a GitHub account. So GitPilot's work can appear as "GitPilot" with an icon in
two complementary ways:

1. **GitHub App (the icon).** When commits/PRs are created through GitPilot's
   GitHub App installation token, GitHub attributes them to ``gitpilota[bot]``
   and shows the App's avatar automatically — set the picture in the App's
   settings (Developer settings → GitHub Apps → GitPilot → Display information).
   This is the cleanest "appears as GitPilot with an icon" path; no commit-message
   change is needed because the App *is* the author.

2. **Co-authored-by trailer (this module).** When a human's token authors the
   commit, append a ``Co-authored-by: GitPilot <…>`` trailer so GitPilot is still
   credited as a contributor with its avatar — exactly how Claude Code attributes
   its commits.

Configure with: ``GITPILOT_COMMIT_ATTRIBUTION`` (on by default), ``GITPILOT_BOT_NAME``,
``GITPILOT_BOT_EMAIL`` (default derives from ``GITHUB_APP_SLUG`` so the avatar
resolves to the App bot).
"""

from __future__ import annotations

import os

SIGNATURE = "\U0001f916 Generated with GitPilot"


def attribution_enabled() -> bool:
    return os.getenv("GITPILOT_COMMIT_ATTRIBUTION", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def bot_name() -> str:
    return os.getenv("GITPILOT_BOT_NAME", "GitPilot")


def bot_email() -> str:
    # Default to the App bot's noreply so the avatar resolves to <slug>[bot].
    # Override with GITPILOT_BOT_EMAIL to point at a real GitPilot bot account.
    slug = os.getenv("GITHUB_APP_SLUG", "gitpilota")
    return os.getenv("GITPILOT_BOT_EMAIL", f"{slug}[bot]@users.noreply.github.com")


def co_authored_by() -> str:
    return f"Co-authored-by: {bot_name()} <{bot_email()}>"


def with_attribution(message: str) -> str:
    """Append the GitPilot signature + Co-authored-by trailer to a commit message.

    Idempotent (won't double‑add) and a no‑op when attribution is disabled.
    """
    msg = (message or "").rstrip()
    if not attribution_enabled() or co_authored_by() in msg:
        return msg
    trailer = f"{SIGNATURE}\n\n{co_authored_by()}"
    return f"{msg}\n\n{trailer}" if msg else trailer
