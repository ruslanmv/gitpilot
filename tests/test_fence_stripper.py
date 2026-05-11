"""Regression tests for the markdown-fence stripper used on agent
file-content output.

The Code Writer agent's prompt asks it to return ONLY the file body —
never a markdown code block.  In practice small and even large LLMs
wrap the output in ``` ``` ``` or ``~~~~`` anyway.  Before this fix
the inline stripper at two call sites in :mod:`gitpilot.agentic` only
handled the simplest case (whole payload is a single fenced block,
opening line is ```` ``` ````, closing line is exactly ```` ``` ````).
This helper hardens against the variants observed in production:

* leading language tag (``` ```python ... ``` ``)
* tilde fences
* trailing prose
* embedded block inside explanatory text

The exact payload from session a0551fb1 (the Nuclear-Physics repo
trace) is included as a test so the regression cannot reappear.
"""
from __future__ import annotations

import pytest

from gitpilot.agentic import _strip_markdown_fences


# ----------------------------------------------------------------------
# Happy paths — bare content untouched
# ----------------------------------------------------------------------

def test_bare_content_passes_through() -> None:
    body = "import os\n\nprint('hello')\n"
    assert _strip_markdown_fences(body) == body.strip()


def test_empty_string_unchanged() -> None:
    assert _strip_markdown_fences("") == ""


def test_none_passes_through() -> None:
    assert _strip_markdown_fences(None) is None  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Plain triple-backtick fences
# ----------------------------------------------------------------------

def test_simple_triple_backtick_block() -> None:
    payload = "```\nimport os\nprint('hi')\n```"
    assert _strip_markdown_fences(payload) == "import os\nprint('hi')"


def test_language_tagged_block() -> None:
    payload = "```python\nfrom pathlib import Path\nprint(Path.cwd())\n```"
    assert _strip_markdown_fences(payload) == "from pathlib import Path\nprint(Path.cwd())"


def test_leading_and_trailing_whitespace_stripped() -> None:
    payload = "\n\n  ```python\nimport os\n```  \n"
    assert _strip_markdown_fences(payload) == "import os"


# ----------------------------------------------------------------------
# Tilde fences (CommonMark variant)
# ----------------------------------------------------------------------

def test_tilde_fence_block() -> None:
    payload = "~~~python\nimport os\n~~~"
    assert _strip_markdown_fences(payload) == "import os"


# ----------------------------------------------------------------------
# Fence embedded in prose — pick the largest body
# ----------------------------------------------------------------------

def test_fence_inside_prose_extracts_body() -> None:
    payload = (
        "Here is the file:\n"
        "```python\n"
        "from pathlib import Path\n\n"
        "def main():\n"
        "    print(Path.cwd())\n"
        "```\n"
        "Let me know if you'd like changes."
    )
    out = _strip_markdown_fences(payload)
    assert "from pathlib import Path" in out
    assert "def main" in out
    assert "Here is the file" not in out
    assert "Let me know" not in out


def test_multiple_fences_returns_largest() -> None:
    payload = (
        "```\nsmall\n```\n"
        "explanation\n"
        "```python\n"
        "# the actual file\nimport sys\nsys.exit(0)\n"
        "```\n"
    )
    out = _strip_markdown_fences(payload)
    assert "the actual file" in out
    assert "small" not in out


# ----------------------------------------------------------------------
# Production payload — session a0551fb1
# ----------------------------------------------------------------------

def test_exact_production_payload_from_trace() -> None:
    """The Final Answer the Code Writer returned in the failing
    Nuclear-Physics session.  Without fence-stripping this would have
    committed a file whose first byte was a backtick."""
    payload = (
        "```\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "\n"
        "def plot_shell_model():\n"
        "    # Generating dummy data for shell model first shells\n"
        "    shells = np.array([1, 2, 3, 4, 5])\n"
        "    energies = np.array([0.0, 1.0, 2.5, 4.0, 6.5])\n"
        "\n"
        "    plt.figure(figsize=(8, 5))\n"
        "    plt.plot(shells, energies, marker='o')\n"
        "    plt.title(\"Shell Model First Shells Energies\")\n"
        "    plt.xlabel(\"Shells\")\n"
        "    plt.ylabel(\"Energy (MeV)\")\n"
        "    plt.xticks(shells)\n"
        "    plt.grid()\n"
        "    plt.show()\n"
        "\n"
        "plot_shell_model()\n"
        "```"
    )
    out = _strip_markdown_fences(payload)
    # No backticks anywhere in the committed file.
    assert "```" not in out
    # The actual code is preserved.
    assert "import matplotlib.pyplot as plt" in out
    assert "plot_shell_model()" in out
    # First and last lines are real code, not fences.
    lines = out.splitlines()
    assert lines[0] == "import matplotlib.pyplot as plt"
    assert lines[-1] == "plot_shell_model()"


# ----------------------------------------------------------------------
# Defensive — never corrupt content if no clean fence is detected
# ----------------------------------------------------------------------

def test_unmatched_fence_returns_input_unchanged() -> None:
    payload = "```python\nimport os\n"  # opening fence, no closing
    assert _strip_markdown_fences(payload) == payload.strip()


def test_only_opening_fence_with_text_after() -> None:
    payload = "```\ntext without closing fence"
    out = _strip_markdown_fences(payload)
    # No corruption — original content kept (caller decides what to do).
    assert "text without closing fence" in out
