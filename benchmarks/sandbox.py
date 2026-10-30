"""Run model-written code against hidden tests without trusting it.

Two layers, because neither is enough alone on a developer laptop:

1. A static check refuses anything a pure, standard-library function has no
   business doing: importing outside a small allowlist, file or process access,
   ``exec``/``eval``, and dunder escapes such as ``__subclasses__``.
2. What passes runs in a separate, isolated interpreter (``python -I``) in a
   throwaway directory, with a scrubbed environment and a hard timeout.

This is a mitigation, not a container. Benchmarking untrusted models at scale
belongs in a disposable VM or container (see README).
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ALLOWED_IMPORTS = frozenset({
    "bisect", "collections", "copy", "dataclasses", "decimal", "enum", "fractions",
    "functools", "heapq", "itertools", "math", "operator", "re", "statistics",
    "string", "typing", "__future__",
})
FORBIDDEN_NAMES = frozenset({
    "__import__", "breakpoint", "compile", "eval", "exec", "exit", "globals",
    "help", "input", "locals", "open", "quit", "vars",
})
FORBIDDEN_ATTRIBUTES = frozenset({
    "__bases__", "__builtins__", "__class__", "__code__", "__dict__", "__globals__",
    "__mro__", "__subclasses__", "__getattribute__", "__loader__", "__spec__",
})

_FENCE = re.compile(r"```(?:python|py)?[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_OPEN_FENCE = re.compile(r"```(?:python|py)?[^\n]*\n(.*)\Z", re.DOTALL | re.IGNORECASE)


def extract_code(text: str) -> str:
    """The model's code: the longest fenced block, else an unclosed one, else the reply.

    Some models open a fence and stop without closing it (llama3 does this
    reliably); the code after the opening line is still the answer.
    """
    blocks = _FENCE.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip()
    unclosed = _OPEN_FENCE.search(text or "")
    if unclosed:
        return unclosed.group(1).strip()
    return (text or "").strip()


def static_check(code: str) -> str:
    """Empty string when the code is acceptable, else the reason it is not."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} (line {exc.lineno})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    return f"imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                return f"imports {node.module}"
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"uses {node.id}"
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            return f"touches {node.attr}"
    return ""


@dataclass
class ExecutionResult:
    passed: bool
    detail: str


def run_tests(code: str, tests: str, *, timeout_s: float = 10.0) -> ExecutionResult:
    """Run ``code`` followed by ``tests`` (plain asserts) in an isolated interpreter."""
    reason = static_check(code)
    if reason:
        return ExecutionResult(False, f"rejected before running: {reason}")

    with tempfile.TemporaryDirectory(prefix="gpbench-") as workdir:
        script = Path(workdir) / "solution_test.py"
        script.write_text(f"{code}\n\n# --- hidden tests ---\n{tests}\nprint('ALL TESTS PASSED')\n",
                          encoding="utf-8")
        env = {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        try:
            completed = subprocess.run(  # noqa: S603 - fixed interpreter, no shell
                [sys.executable, "-I", "-S", str(script)],
                cwd=workdir, env=env, capture_output=True, text=True,
                timeout=timeout_s, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, f"timed out after {timeout_s:.0f}s")

    if completed.returncode == 0 and "ALL TESTS PASSED" in completed.stdout:
        return ExecutionResult(True, "all hidden tests passed")
    tail = (completed.stderr or completed.stdout).strip().splitlines()
    last = tail[-1] if tail else f"exit code {completed.returncode}"
    return ExecutionResult(False, last[:300])
