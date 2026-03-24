"""HuggingFace Space management tools for GitPilot.

Provides CrewAI-compatible tools for:
- Cloning HF Spaces
- Analyzing Space health (SDK, deps, dead patterns)
- Generating fixes via OllaBridge LLM
- Pushing fixes to HF repos
- Managing ZeroGPU hardware allocation

Designed to work with GitPilot's multi-agent architecture
and OllaBridge Cloud as the LLM backend.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Dead/deprecated patterns to scan for
DEAD_PATTERNS: list[tuple[str, str]] = [
    (r'st\.secrets\[.*BACKEND_SERVER.*\]', 'Dead backend server dependency'),
    (r'api-inference\.huggingface\.co', 'Deprecated HF Inference API endpoint'),
    (r'from\s+dalle_mini', 'Deprecated dalle-mini imports'),
    (r'from\s+min_dalle', 'Deprecated min-dalle imports'),
    (r'from\s+transformers\.file_utils', 'Removed transformers.file_utils'),
    (r'jax\.experimental\.PartitionSpec', 'Moved JAX PartitionSpec API'),
    (r'gr\.inputs\.', 'Deprecated Gradio inputs API'),
    (r'gr\.outputs\.', 'Deprecated Gradio outputs API'),
]


def clone_hf_space(space_id: str, token: str | None = None) -> dict[str, Any]:
    """Clone a HuggingFace Space repository to a temp directory.

    Args:
        space_id: Full Space ID (e.g. 'user/space-name').
        token: Optional HF token for private repos.

    Returns:
        Dict with 'path' (str), 'success' (bool), 'error' (str|None).
    """
    tmpdir = tempfile.mkdtemp(prefix="gitpilot_hf_")
    name = space_id.split("/")[-1]
    repo_dir = os.path.join(tmpdir, name)

    clone_url = f"https://huggingface.co/spaces/{space_id}"
    if token:
        clone_url = f"https://user:{token}@huggingface.co/spaces/{space_id}"

    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", clone_url, repo_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"path": "", "success": False, "error": result.stderr.strip()}
        return {"path": repo_dir, "success": True, "error": None}
    except subprocess.TimeoutExpired:
        return {"path": "", "success": False, "error": "Clone timed out (120s)"}
    except Exception as exc:
        return {"path": "", "success": False, "error": str(exc)}


def analyze_hf_space(repo_dir: str) -> dict[str, Any]:
    """Analyze a cloned HuggingFace Space for issues.

    Returns a diagnosis dict with:
        sdk, app_file, issues, dead_patterns, needs_gpu,
        needs_rebuild, severity, recommendations.
    """
    path = Path(repo_dir)
    diag: dict[str, Any] = {
        "sdk": "unknown",
        "app_file": "app.py",
        "issues": [],
        "dead_patterns": [],
        "needs_gpu": False,
        "needs_rebuild": False,
        "severity": "info",
        "recommendations": [],
        "files": [],
    }

    # Parse README front matter
    readme = path / "README.md"
    if readme.exists():
        text = readme.read_text(errors="replace")
        sdk_match = re.search(r'^sdk:\s*(\S+)', text, re.MULTILINE)
        app_match = re.search(r'^app_file:\s*(\S+)', text, re.MULTILINE)
        if sdk_match:
            diag["sdk"] = sdk_match.group(1)
        if app_match:
            diag["app_file"] = app_match.group(1)
    else:
        diag["issues"].append("Missing README.md")

    # Check app_file exists
    app_path = path / diag["app_file"]
    if not app_path.exists():
        diag["issues"].append(f"app_file '{diag['app_file']}' does not exist")
        diag["severity"] = "critical"
        diag["needs_rebuild"] = True

    # Check requirements.txt
    req = path / "requirements.txt"
    if not req.exists():
        diag["issues"].append("Missing requirements.txt")
    elif not req.read_text(errors="replace").strip():
        diag["issues"].append("Empty requirements.txt")

    # Scan for dead patterns
    for py_file in path.rglob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
        except OSError:
            continue
        for pattern, desc in DEAD_PATTERNS:
            if re.search(pattern, content):
                rel = str(py_file.relative_to(path))
                diag["dead_patterns"].append(f"{rel}: {desc}")
                diag["issues"].append(f"Dead pattern in {rel}: {desc}")
                diag["severity"] = "critical"
                diag["needs_rebuild"] = True

    # Check GPU needs
    gpu_indicators = [
        "torch", "diffusers", "transformers", "accelerate",
        "spaces.GPU", "@spaces.GPU", "cuda", ".to(\"cuda\")",
    ]
    for py_file in path.rglob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
        except OSError:
            continue
        for indicator in gpu_indicators:
            if indicator in content:
                diag["needs_gpu"] = True
                break
        if diag["needs_gpu"]:
            break

    # File listing
    for p in sorted(path.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            diag["files"].append(str(p.relative_to(path)))

    # Build recommendations
    if diag["needs_rebuild"]:
        diag["recommendations"].append("Rebuild app.py with modern dependencies")
        if diag["sdk"] == "streamlit":
            diag["recommendations"].append("Consider migrating to Gradio SDK")
    if diag["dead_patterns"]:
        diag["recommendations"].append("Remove deprecated API calls")
    if diag["needs_gpu"]:
        diag["recommendations"].append("Request ZeroGPU (zero-a10g) hardware")

    return diag


def generate_space_fix(
    space_id: str,
    diagnosis: dict[str, Any],
    app_content: str = "",
    ollabridge_url: str | None = None,
    ollabridge_model: str = "qwen2.5:1.5b",
    ollabridge_key: str | None = None,
) -> dict[str, Any]:
    """Generate a fix for a broken HF Space.

    If ollabridge_url is provided, uses LLM for intelligent fix.
    Otherwise falls back to template-based fix.

    Returns dict with 'files' (dict of filename->content), 'explanation' (str).
    """
    # Try LLM-powered fix via OllaBridge
    if ollabridge_url:
        try:
            import httpx
            prompt = _build_repair_prompt(space_id, diagnosis, app_content)
            payload = {
                "model": ollabridge_model,
                "messages": [
                    {"role": "system", "content": "You are an expert HuggingFace Spaces developer. Output valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            }
            headers = {"Content-Type": "application/json"}
            if ollabridge_key:
                headers["Authorization"] = f"Bearer {ollabridge_key}"

            resp = httpx.post(
                f"{ollabridge_url.rstrip('/')}/v1/chat/completions",
                json=payload, headers=headers, timeout=120.0,
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                fix = _parse_llm_fix(content)
                if fix:
                    return fix
        except Exception as exc:
            logger.warning("OllaBridge fix generation failed: %s", exc)

    # Template fallback
    return _generate_template_fix(space_id, diagnosis)


def push_space_fix(
    repo_dir: str,
    fix: dict[str, Any],
    commit_message: str = "fix: auto-repair by GitPilot + RepoGuardian",
) -> dict[str, Any]:
    """Apply fix files and push to the Space repo.

    Args:
        repo_dir: Path to cloned Space repo.
        fix: Fix dict with 'files' key.
        commit_message: Git commit message.

    Returns:
        Dict with 'success' (bool), 'changed_files' (list), 'error' (str|None).
    """
    path = Path(repo_dir)
    changed = []

    # Write fix files
    for filename, content in fix.get("files", {}).items():
        filepath = path / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
        changed.append(filename)

    if not changed:
        return {"success": False, "changed_files": [], "error": "No files to write"}

    # Git add, commit, push
    cmds = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", commit_message],
        ["git", "push", "origin", "main"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return {
                "success": False,
                "changed_files": changed,
                "error": f"Command '{' '.join(cmd)}' failed: {result.stderr.strip()}",
            }

    return {"success": True, "changed_files": changed, "error": None}


def manage_space_hardware(
    space_id: str,
    token: str,
    hardware: str = "zero-a10g",
    auto_free: bool = True,
) -> dict[str, Any]:
    """Request hardware for a HuggingFace Space.

    If ZeroGPU slots are full and auto_free is True,
    automatically downgrades a paused Space to free a slot.

    Returns dict with 'success', 'hardware', 'freed_slot', 'error'.
    """
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token)

        # Try direct request
        try:
            api.request_space_hardware(space_id, hardware)
            return {"success": True, "hardware": hardware, "freed_slot": None, "error": None}
        except Exception as exc:
            if "limited to" not in str(exc).lower():
                return {"success": False, "hardware": None, "freed_slot": None, "error": str(exc)}

        if not auto_free:
            return {"success": False, "hardware": None, "freed_slot": None, "error": "Slots full, auto_free disabled"}

        # Find and downgrade a paused Space
        namespace = space_id.split("/")[0]
        spaces = list(api.list_spaces(author=namespace))
        for s in spaces:
            try:
                info = api.space_info(s.id)
                if not info.runtime:
                    continue
                raw_hw = info.runtime.raw.get("hardware", {})
                req_hw = raw_hw.get("requested", "")
                stage = info.runtime.stage
                if "zero" in str(req_hw).lower() and stage in ("PAUSED", "SLEEPING") and s.id != space_id:
                    api.request_space_hardware(s.id, "cpu-basic")
                    # Retry the original request
                    api.request_space_hardware(space_id, hardware)
                    return {
                        "success": True,
                        "hardware": hardware,
                        "freed_slot": s.id,
                        "error": None,
                    }
            except Exception:
                continue

        return {"success": False, "hardware": None, "freed_slot": None, "error": "No paused Spaces to free"}

    except ImportError:
        return {"success": False, "hardware": None, "freed_slot": None, "error": "huggingface_hub not installed"}


def get_space_runtime_info(space_id: str, token: str | None = None) -> dict[str, Any]:
    """Fetch runtime info for a HuggingFace Space.

    Returns dict with sdk, stage, hardware, domain, etc.
    """
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token)
        info = api.space_info(space_id)
        result: dict[str, Any] = {
            "space_id": space_id,
            "sdk": info.sdk,
            "success": True,
        }
        if info.runtime:
            result["stage"] = info.runtime.stage
            hw = info.runtime.raw.get("hardware", {})
            result["current_hardware"] = hw.get("current")
            result["requested_hardware"] = hw.get("requested")
            domains = info.runtime.raw.get("domains", [])
            if domains:
                result["domain"] = domains[0].get("domain")
        return result
    except Exception as exc:
        return {"space_id": space_id, "success": False, "error": str(exc)}


# ---- Internal helpers ----

def _build_repair_prompt(space_id: str, diagnosis: dict[str, Any], app_content: str) -> str:
    return f"""A HuggingFace Space is broken and needs repair.

## Space: {space_id}
- SDK: {diagnosis.get('sdk', 'unknown')}
- app_file: {diagnosis.get('app_file', 'app.py')}

## Issues
{chr(10).join('- ' + i for i in diagnosis.get('issues', []))}

## Dead Patterns
{chr(10).join('- ' + p for p in diagnosis.get('dead_patterns', []))}

## Current app.py (first 150 lines)
{app_content[:5000]}

Generate a complete fix as JSON:
{{
  "files": {{
    "app.py": "<complete new app.py>",
    "requirements.txt": "<complete new requirements.txt>",
    "README.md": "<complete new README.md with YAML front matter>"
  }},
  "explanation": "<what was fixed>"
}}"""


def _parse_llm_fix(response: str) -> dict[str, Any] | None:
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    match = re.search(r'```(?:json)?\s*\n(.+?)\n```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _generate_template_fix(space_id: str, diagnosis: dict[str, Any]) -> dict[str, Any]:
    name = space_id.split("/")[-1]
    title = name.replace("-", " ").replace("_", " ").title()
    needs_gpu = diagnosis.get("needs_gpu", False)

    if needs_gpu:
        app = f'''"""\n{title} - Auto-repaired by GitPilot + RepoGuardian\n"""\nimport gradio as gr\nimport numpy as np\n\ntry:\n    import spaces\n    GPU = True\nexcept ImportError:\n    GPU = False\n\ndef process(prompt: str, progress=gr.Progress(track_tqdm=True)):\n    if not prompt.strip():\n        raise gr.Error("Please enter a prompt.")\n    return f"Output for: {{prompt}}"\n\nif GPU:\n    process = spaces.GPU(process)\n\nwith gr.Blocks(theme=gr.themes.Soft(), title="{title}") as demo:\n    gr.Markdown("# {title}")\n    with gr.Row():\n        inp = gr.Textbox(label="Prompt", lines=3)\n        out = gr.Textbox(label="Output", lines=5)\n    gr.Button("Generate", variant="primary").click(process, [inp], [out])\n\nif __name__ == "__main__":\n    demo.launch()\n'''
        reqs = "gradio>=4.0.0\ntorch>=2.0.0\nnumpy>=1.24.0\n"
    else:
        app = f'''"""\n{title} - Auto-repaired by GitPilot + RepoGuardian\n"""\nimport gradio as gr\n\ndef process(text: str):\n    if not text.strip():\n        raise gr.Error("Please enter text.")\n    return f"Processed: {{text}}"\n\nwith gr.Blocks(theme=gr.themes.Soft(), title="{title}") as demo:\n    gr.Markdown("# {title}")\n    with gr.Row():\n        inp = gr.Textbox(label="Input", lines=3)\n        out = gr.Textbox(label="Output", lines=3)\n    gr.Button("Process", variant="primary").click(process, [inp], [out])\n\nif __name__ == "__main__":\n    demo.launch()\n'''
        reqs = "gradio>=4.0.0\n"

    readme = f"""---\ntitle: {title}\nemoji: \U0001f680\ncolorFrom: blue\ncolorTo: purple\nsdk: gradio\nsdk_version: 5.23.0\napp_file: app.py\npinned: false\nlicense: apache-2.0\n---\n\n# {title}\n\nAuto-repaired by [GitPilot](https://github.com/ruslanmv/gitpilot) + [RepoGuardian](https://github.com/ruslanmv/RepoGuardian).\n"""

    return {
        "files": {"app.py": app, "requirements.txt": reqs, "README.md": readme},
        "explanation": "Template fix: replaced broken app with working Gradio placeholder",
    }
