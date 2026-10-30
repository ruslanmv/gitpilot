"""Benchmark suites: each is a list of tasks with a deterministic grader.

A task sends the same messages to every model, and its grader turns the reply
into pass or fail with a reason. Nothing is judged by another model: every
verdict is a unit test, a structural match or a string rule, so two runs of the
same reply always score the same.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..client import ChatResult


@dataclass(frozen=True)
class Verdict:
    passed: bool
    detail: str = ""


@dataclass
class Task:
    """One prompt and how to grade the answer."""

    id: str
    suite: str
    category: str
    messages: List[Dict[str, Any]]
    grade: Callable[[ChatResult], Verdict]
    tools: Optional[List[Dict[str, Any]]] = None
    max_tokens: int = 1024
    #: A reply known to pass, used by the tests to prove the grader can be satisfied.
    reference: Optional[ChatResult] = None
    #: Free-form facts for the task digest (tests, expected values).
    spec: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> Dict[str, Any]:
        return {"id": self.id, "suite": self.suite, "messages": self.messages,
                "tools": self.tools, "max_tokens": self.max_tokens, "spec": self.spec}


SUITE_TITLES = {
    "code": "Coding",
    "tools": "Tool calling",
    "instructions": "Instruction following",
    "reasoning": "Reasoning",
    "agentic": "GitPilot agent tasks",
}

#: Suites that run through chat completions (``agentic`` is driven separately).
CHAT_SUITES = ("code", "tools", "instructions", "reasoning")


def load_tasks(suites: List[str]) -> List[Task]:
    from . import code, instructions, reasoning, tools

    registry = {
        "code": code.TASKS,
        "tools": tools.TASKS,
        "instructions": instructions.TASKS,
        "reasoning": reasoning.TASKS,
    }
    tasks: List[Task] = []
    for suite in suites:
        tasks.extend(registry.get(suite, []))
    return tasks


def task_set_digest(tasks: List[Task]) -> str:
    """Hash of everything that defines the task set, for the run manifest."""
    blob = json.dumps([task.fingerprint() for task in tasks], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def user(text: str) -> List[Dict[str, Any]]:
    return [{"role": "user", "content": text}]


def text_reply(text: str) -> ChatResult:
    return ChatResult(text=text)


def call_reply(name: str, arguments: Dict[str, Any]) -> ChatResult:
    return ChatResult(tool_calls=[{"name": name, "arguments": arguments}])
