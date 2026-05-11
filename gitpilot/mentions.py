# gitpilot/mentions.py
"""@-mention parser for chat input — additive context expander.

Recognised tokens (additive, non-destructive — unknown tokens are left as-is)::

    @/abs/path                 — single file (path under workspace)
    @./rel/path                — relative path resolved against workspace
    @glob:src/**/*.ts          — file glob expanded under workspace
    @problems                  — current diagnostics (read from .gitpilot/problems.json
                                 if present, otherwise empty)
    @commit:<sha>              — `git show <sha>` summary
    @diff:<range>              — `git diff <range>` summary
    @selection                 — selection sent from the editor (falls back to
                                 the GITPILOT_SELECTION env var)
    @pr:<n>                    — placeholder block; resolved by API layer

The parser is intentionally pure-Python and side-effect-free except for
shelling out to git when a commit/diff mention is encountered.  All output
is size-capped so a noisy mention can never blow the prompt budget.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 16_000
MAX_GLOB_FILES = 12
MAX_DIAGNOSTICS = 50
MAX_GIT_OUTPUT = 8_000

# A mention starts with @ and runs until whitespace OR the next @ that is
# clearly the start of a fresh token (preceded by whitespace).  We scan
# greedily on the leading @ then stop at whitespace.
_MENTION_RE = re.compile(r"(?<!\w)@([^\s@]+)")


@dataclass
class ExpandedMention:
    """A single mention and the text it expanded to."""

    token: str          # the raw @-token without the leading @
    kind: str           # "file" | "glob" | "problems" | "commit" | "diff" | "selection" | "pr" | "unknown"
    body: str           # text injected into the context (markdown-formatted)
    error: Optional[str] = None


@dataclass
class MentionResult:
    """Result of parsing a user message for @-mentions."""

    cleaned_message: str
    expansions: List[ExpandedMention] = field(default_factory=list)

    def to_context_block(self) -> str:
        """Render expansions as a single markdown block, or '' if none."""
        if not self.expansions:
            return ""
        parts = ["## Mentions"]
        for exp in self.expansions:
            head = f"### `@{exp.token}` ({exp.kind})"
            if exp.error:
                parts.append(f"{head}\n\n_error: {exp.error}_")
            else:
                parts.append(f"{head}\n\n{exp.body}")
        return "\n\n".join(parts)


class MentionParser:
    """Parse and expand @-mentions in a chat message."""

    def __init__(
        self,
        workspace_path: Path,
        *,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_glob_files: int = MAX_GLOB_FILES,
    ) -> None:
        self.workspace_path = workspace_path.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_glob_files = max_glob_files

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def parse(self, message: str) -> MentionResult:
        if not message:
            return MentionResult(cleaned_message=message)

        expansions: List[ExpandedMention] = []
        for match in _MENTION_RE.finditer(message):
            token = match.group(1)
            expansions.append(self._expand_token(token))

        return MentionResult(cleaned_message=message, expansions=expansions)

    # ------------------------------------------------------------------
    # Token dispatch
    # ------------------------------------------------------------------
    def _expand_token(self, token: str) -> ExpandedMention:
        try:
            if token == "problems":
                return self._expand_problems(token)
            if token == "selection":
                return self._expand_selection(token)
            if token.startswith("glob:"):
                return self._expand_glob(token, token[5:])
            if token.startswith("commit:"):
                return self._expand_commit(token, token[7:])
            if token.startswith("diff:"):
                return self._expand_diff(token, token[5:])
            if token.startswith("pr:"):
                return ExpandedMention(
                    token=token,
                    kind="pr",
                    body=f"_PR reference `{token[3:]}` will be resolved by the API layer._",
                )
            # Path-like: @/..., @./..., @../..., @name/path
            if token.startswith(("/", "./", "../")) or "/" in token or token.endswith(
                (".py", ".ts", ".tsx", ".js", ".md", ".json", ".yaml", ".yml")
            ):
                return self._expand_file(token, token)
            return ExpandedMention(token=token, kind="unknown", body="", error="unrecognised token")
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("mention %s failed", token, exc_info=True)
            return ExpandedMention(token=token, kind="unknown", body="", error=str(exc))

    # ------------------------------------------------------------------
    # Expanders
    # ------------------------------------------------------------------
    def _resolve_under_workspace(self, raw: str) -> Path:
        if raw.startswith("/"):
            # Allow absolute paths but only if they live under the workspace.
            p = Path(raw).resolve()
        else:
            p = (self.workspace_path / raw.lstrip("./")).resolve()
        if not str(p).startswith(str(self.workspace_path)):
            raise PermissionError(f"path escapes workspace: {raw}")
        return p

    def _expand_file(self, token: str, raw: str) -> ExpandedMention:
        path = self._resolve_under_workspace(raw)
        if not path.exists() or not path.is_file():
            return ExpandedMention(token=token, kind="file", body="", error="not found")
        data = path.read_bytes()[: self.max_file_bytes]
        text = data.decode("utf-8", errors="replace")
        rel = path.relative_to(self.workspace_path)
        body = f"```{_guess_lang(path)} title={rel}\n{text}\n```"
        return ExpandedMention(token=token, kind="file", body=body)

    def _expand_glob(self, token: str, pattern: str) -> ExpandedMention:
        files = sorted(self.workspace_path.glob(pattern))[: self.max_glob_files]
        if not files:
            return ExpandedMention(token=token, kind="glob", body="", error="no matches")
        rel = [str(p.relative_to(self.workspace_path)) for p in files]
        body = "Matched files:\n" + "\n".join(f"- `{r}`" for r in rel)
        return ExpandedMention(token=token, kind="glob", body=body)

    def _expand_problems(self, token: str) -> ExpandedMention:
        path = self.workspace_path / ".gitpilot" / "problems.json"
        if not path.exists():
            return ExpandedMention(token=token, kind="problems", body="_no diagnostics file present_")
        try:
            items = json.loads(path.read_text())[:MAX_DIAGNOSTICS]
        except Exception as e:
            return ExpandedMention(token=token, kind="problems", body="", error=str(e))
        lines = []
        for it in items:
            sev = it.get("severity", "info")
            file_ = it.get("file", "?")
            line = it.get("line", "?")
            msg = it.get("message", "")
            lines.append(f"- [{sev}] {file_}:{line} — {msg}")
        return ExpandedMention(token=token, kind="problems", body="\n".join(lines) or "_no diagnostics_")

    def _expand_selection(self, token: str) -> ExpandedMention:
        text = os.environ.get("GITPILOT_SELECTION", "")
        if not text:
            return ExpandedMention(token=token, kind="selection", body="", error="no selection")
        return ExpandedMention(token=token, kind="selection", body=f"```\n{text[:self.max_file_bytes]}\n```")

    def _expand_commit(self, token: str, sha: str) -> ExpandedMention:
        out = self._git("show", "--stat", "--patch", sha)
        if out is None:
            return ExpandedMention(token=token, kind="commit", body="", error="git failed")
        return ExpandedMention(token=token, kind="commit", body=f"```diff\n{out[:MAX_GIT_OUTPUT]}\n```")

    def _expand_diff(self, token: str, rng: str) -> ExpandedMention:
        out = self._git("diff", "--stat", "--patch", rng)
        if out is None:
            return ExpandedMention(token=token, kind="diff", body="", error="git failed")
        return ExpandedMention(token=token, kind="diff", body=f"```diff\n{out[:MAX_GIT_OUTPUT]}\n```")

    def _git(self, *args: str) -> Optional[str]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.workspace_path),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode != 0:
                return None
            return proc.stdout
        except Exception:
            return None


_LANG_BY_EXT = {
    ".py": "python", ".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "jsx",
    ".rs": "rust", ".go": "go", ".java": "java", ".rb": "ruby",
    ".md": "md", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".sql": "sql", ".sh": "bash",
}


def _guess_lang(path: Path) -> str:
    return _LANG_BY_EXT.get(path.suffix.lower(), "")


def expand(message: str, workspace_path: Path) -> MentionResult:
    """Module-level convenience wrapper."""
    return MentionParser(workspace_path).parse(message)
