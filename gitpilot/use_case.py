# gitpilot/use_case.py
"""Use Case manager — guided requirement clarification and spec generation.

Non-destructive, additive feature.  Stores use cases under:
    ~/.gitpilot/workspaces/{owner}/{repo}/.gitpilot/context/use_cases/

Each use case is a JSON file with structured spec + message history.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class UseCaseSpec:
    """Structured spec extracted from use-case conversation."""
    title: str = ""
    summary: str = ""
    problem: str = ""
    users: str = ""
    requirements: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class UseCaseMessage:
    role: str  # "user" or "assistant"
    content: str
    ts: str = ""


@dataclass
class UseCase:
    use_case_id: str
    title: str
    created_at: str
    updated_at: str
    is_active: bool = False
    is_finalized: bool = False
    spec: UseCaseSpec = field(default_factory=UseCaseSpec)
    messages: List[UseCaseMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_summary(self) -> dict:
        return {
            "use_case_id": self.use_case_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_active": self.is_active,
            "is_finalized": self.is_finalized,
        }


# ---------------------------------------------------------------------------
# Guided assistant prompts
# ---------------------------------------------------------------------------
GUIDED_SYSTEM_PROMPT = """\
You are a requirements analyst helping clarify a software use case.
Your job is to ask structured questions and extract a clear spec.

After each user message, do TWO things:
1. Respond conversationally (acknowledge, ask follow-up questions)
2. Update the structured spec with any new information

Focus on extracting:
- Summary: what is being built
- Problem: what problem it solves
- Users/Personas: who will use it
- Requirements: functional requirements (bullet list)
- Acceptance Criteria: how to verify it works (bullet list)
- Constraints: technical or business constraints
- Open Questions: anything still unclear

Be concise but thorough. Ask one or two questions at a time.
"""

INITIAL_ASSISTANT_MESSAGE = (
    "Welcome! Let's define this use case together.\n\n"
    "To get started, could you describe:\n"
    "1. **What** you want to build (high-level summary)\n"
    "2. **Who** will use it (target users)\n"
    "3. **Why** it's needed (the problem it solves)\n\n"
    "You can paste meeting notes, transcripts, or just describe it in your own words."
)


# ---------------------------------------------------------------------------
# Use Case Manager
# ---------------------------------------------------------------------------
class UseCaseManager:
    """Manages use cases stored as JSON files."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.use_cases_dir = workspace_path / ".gitpilot" / "context" / "use_cases"

    def _ensure_dir(self):
        self.use_cases_dir.mkdir(parents=True, exist_ok=True)

    def _uc_path(self, use_case_id: str) -> Path:
        return self.use_cases_dir / f"{use_case_id}.json"

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_use_cases(self) -> List[dict]:
        """Return summary list of all use cases."""
        self._ensure_dir()
        results = []
        for f in sorted(self.use_cases_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "use_case_id": data.get("use_case_id", f.stem),
                    "title": data.get("title", "(untitled)"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "is_active": data.get("is_active", False),
                    "is_finalized": data.get("is_finalized", False),
                })
            except Exception:
                logger.warning("Skipping corrupt use case: %s", f)
        return results

    def create_use_case(self, title: str = "New Use Case", initial_notes: str = "") -> UseCase:
        """Create a new use case with initial assistant message."""
        self._ensure_dir()
        uc_id = uuid.uuid4().hex[:12]
        now = self._now()

        uc = UseCase(
            use_case_id=uc_id,
            title=title,
            created_at=now,
            updated_at=now,
            spec=UseCaseSpec(title=title, notes=initial_notes),
            messages=[
                UseCaseMessage(role="assistant", content=INITIAL_ASSISTANT_MESSAGE, ts=now),
            ],
        )
        self._save(uc)
        return uc

    def get_use_case(self, use_case_id: str) -> Optional[UseCase]:
        """Load a use case by ID."""
        path = self._uc_path(use_case_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return self._from_dict(data)
        except Exception as e:
            logger.warning("Failed to load use case %s: %s", use_case_id, e)
            return None

    def chat(self, use_case_id: str, user_message: str) -> Optional[UseCase]:
        """Process a user message and return updated use case with assistant response.

        This is the guided chat: we parse the user message, update the spec,
        and generate an assistant response with follow-up questions.
        """
        uc = self.get_use_case(use_case_id)
        if not uc:
            return None

        now = self._now()

        # Add user message
        uc.messages.append(UseCaseMessage(role="user", content=user_message, ts=now))

        # Parse and update spec from user message
        self._update_spec_from_message(uc.spec, user_message)

        # Generate assistant response
        response = self._generate_response(uc)
        uc.messages.append(UseCaseMessage(role="assistant", content=response, ts=now))

        uc.updated_at = now
        self._save(uc)
        return uc

    def finalize(self, use_case_id: str) -> Optional[UseCase]:
        """Mark a use case as finalized and active, export markdown."""
        uc = self.get_use_case(use_case_id)
        if not uc:
            return None

        now = self._now()

        # Deactivate all others
        for f in self.use_cases_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("is_active"):
                    data["is_active"] = False
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass

        # Mark this one as active + finalized
        uc.is_active = True
        uc.is_finalized = True
        uc.updated_at = now
        self._save(uc)

        # Export markdown
        self._export_markdown(uc)

        return uc

    def get_active_use_case(self) -> Optional[UseCase]:
        """Return the currently active use case, if any."""
        self._ensure_dir()
        for f in self.use_cases_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("is_active"):
                    return self._from_dict(data)
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _save(self, uc: UseCase):
        path = self._uc_path(uc.use_case_id)
        path.write_text(json.dumps(uc.to_dict(), indent=2), encoding="utf-8")

    def _from_dict(self, data: dict) -> UseCase:
        spec_data = data.get("spec", {})
        spec = UseCaseSpec(
            title=spec_data.get("title", ""),
            summary=spec_data.get("summary", ""),
            problem=spec_data.get("problem", ""),
            users=spec_data.get("users", ""),
            requirements=spec_data.get("requirements", []),
            acceptance_criteria=spec_data.get("acceptance_criteria", []),
            constraints=spec_data.get("constraints", []),
            open_questions=spec_data.get("open_questions", []),
            notes=spec_data.get("notes", ""),
        )
        messages = [
            UseCaseMessage(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                ts=m.get("ts", ""),
            )
            for m in data.get("messages", [])
        ]
        return UseCase(
            use_case_id=data.get("use_case_id", ""),
            title=data.get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            is_active=data.get("is_active", False),
            is_finalized=data.get("is_finalized", False),
            spec=spec,
            messages=messages,
        )

    def _update_spec_from_message(self, spec: UseCaseSpec, message: str):
        """Parse user message and update spec fields heuristically."""
        msg_lower = message.lower()
        lines = [l.strip() for l in message.split("\n") if l.strip()]

        for line in lines:
            ll = line.lower()

            # Detect labeled sections
            if ll.startswith("summary:"):
                spec.summary = line.split(":", 1)[1].strip()
            elif ll.startswith("problem:"):
                spec.problem = line.split(":", 1)[1].strip()
            elif ll.startswith("users:") or ll.startswith("personas:"):
                spec.users = line.split(":", 1)[1].strip()
            elif ll.startswith("notes:"):
                spec.notes = line.split(":", 1)[1].strip()
            elif ll.startswith("constraint:") or ll.startswith("constraints:"):
                val = line.split(":", 1)[1].strip()
                if val and val not in spec.constraints:
                    spec.constraints.append(val)
            elif ll.startswith("- ") or ll.startswith("* "):
                # Bullet items — classify by context
                item = line[2:].strip()
                if not item:
                    continue
                # If it looks like acceptance criteria
                if any(kw in item.lower() for kw in ["should", "must", "verify", "test", "given", "when", "then"]):
                    if item not in spec.acceptance_criteria:
                        spec.acceptance_criteria.append(item)
                else:
                    if item not in spec.requirements:
                        spec.requirements.append(item)

        # If no summary yet and message is substantial, use first sentence
        if not spec.summary and len(message) > 20:
            first_sentence = message.split(".")[0].strip()
            if len(first_sentence) > 10:
                spec.summary = first_sentence[:200]

    def _generate_response(self, uc: UseCase) -> str:
        """Generate a guided assistant response based on current spec state."""
        spec = uc.spec
        missing = []

        if not spec.summary:
            missing.append("a **summary** of what you're building")
        if not spec.problem:
            missing.append("the **problem** this solves")
        if not spec.users:
            missing.append("the **target users/personas**")
        if not spec.requirements:
            missing.append("**functional requirements** (as bullet points)")
        if not spec.acceptance_criteria:
            missing.append("**acceptance criteria** (how to verify it works)")

        if missing:
            items = "\n".join(f"- {m}" for m in missing[:3])
            return (
                f"Thanks for the details! I've updated the spec preview.\n\n"
                f"To make the spec more complete, could you provide:\n{items}\n\n"
                f"You can paste structured info or just describe it naturally."
            )

        # Spec is reasonably complete
        if spec.open_questions:
            q_list = "\n".join(f"- {q}" for q in spec.open_questions[:3])
            return (
                f"The spec is taking shape nicely. There are some open questions:\n{q_list}\n\n"
                f"Would you like to address these, or shall we **Finalize** the use case?"
            )

        return (
            "The spec looks fairly complete! Here's what we have:\n\n"
            f"**Summary:** {spec.summary}\n"
            f"**Requirements:** {len(spec.requirements)} items\n"
            f"**Acceptance Criteria:** {len(spec.acceptance_criteria)} items\n\n"
            "You can add more details or click **Finalize** to save this as the active use case."
        )

    def _export_markdown(self, uc: UseCase):
        """Export use case as a markdown file."""
        spec = uc.spec
        lines = [
            f"# Use Case: {spec.title or uc.title}",
            "",
            f"**ID:** {uc.use_case_id}",
            f"**Created:** {uc.created_at}",
            f"**Finalized:** {uc.updated_at}",
            f"**Status:** {'Active' if uc.is_active else 'Inactive'}",
            "",
        ]

        if spec.summary:
            lines.extend(["## Summary", "", spec.summary, ""])
        if spec.problem:
            lines.extend(["## Problem", "", spec.problem, ""])
        if spec.users:
            lines.extend(["## Users / Personas", "", spec.users, ""])
        if spec.requirements:
            lines.extend(["## Requirements", ""])
            for r in spec.requirements:
                lines.append(f"- {r}")
            lines.append("")
        if spec.acceptance_criteria:
            lines.extend(["## Acceptance Criteria", ""])
            for ac in spec.acceptance_criteria:
                lines.append(f"- {ac}")
            lines.append("")
        if spec.constraints:
            lines.extend(["## Constraints", ""])
            for c in spec.constraints:
                lines.append(f"- {c}")
            lines.append("")
        if spec.open_questions:
            lines.extend(["## Open Questions", ""])
            for q in spec.open_questions:
                lines.append(f"- {q}")
            lines.append("")
        if spec.notes:
            lines.extend(["## Notes", "", spec.notes, ""])

        md_path = self.use_cases_dir / f"{uc.use_case_id}.md"
        md_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Exported use case markdown: %s", md_path)
