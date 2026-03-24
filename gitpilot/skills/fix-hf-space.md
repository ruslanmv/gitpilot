# /fix-hf-space

Analyze and repair a broken HuggingFace Space

## Description

This skill diagnoses and fixes broken HuggingFace Spaces by:
1. Cloning the Space repository
2. Analyzing for dead dependencies, deprecated APIs, and SDK issues
3. Generating a complete fix using OllaBridge LLM (or template fallback)
4. Pushing the fix and managing ZeroGPU hardware if needed

Works with RepoGuardian's Space analyzer for structured diagnosis.

## Arguments

- `space_id` (required): HuggingFace Space ID, e.g. `ruslanmv/Logo-Creator`
- `--push`: Push fixes to the Space repo (default: dry run)
- `--hardware`: Also manage ZeroGPU hardware allocation

## Prompt

Fix the broken HuggingFace Space `{space_id}`.

Steps:
1. Clone the Space: `clone_hf_space("{space_id}")`
2. Get runtime info: `get_space_runtime_info("{space_id}")`
3. Analyze for issues: `analyze_hf_space(repo_dir)`
4. Generate fix: `generate_space_fix("{space_id}", diagnosis, app_content)`
5. Push fix: `push_space_fix(repo_dir, fix)`
6. Manage hardware: `manage_space_hardware("{space_id}", token, "zero-a10g")`

Use OllaBridge Cloud ({ollabridge_url}) for intelligent analysis.
Report all issues found and actions taken.

## Example

```bash
gitpilot skill fix-hf-space ruslanmv/Logo-Creator --push --hardware
```

## Required Tools

- `clone_hf_space`
- `analyze_hf_space`
- `generate_space_fix`
- `push_space_fix`
- `manage_space_hardware`
- `get_space_runtime_info`
