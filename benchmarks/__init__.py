"""Small model-quality benchmarks for GitPilot.

Runs fresh, programmatically graded tasks (coding, tool calling, instruction
following, reasoning, and optionally GitPilot's own agent tasks) against any
OpenAI-compatible endpoint — OllaBridge by default — and renders an HTML report.

    python -m benchmarks.run --models-file benchmarks/models.txt

See ``benchmarks/README.md`` for the methodology.
"""

HARNESS_VERSION = "1.0.0"
