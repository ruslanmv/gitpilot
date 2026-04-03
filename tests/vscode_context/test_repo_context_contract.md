# Repo-context upgrade contract

This patch adds a non-destructive repo-aware context pipeline for the VS Code extension.

Expected behavior:
- current workspace or repo root is scanned into a compact tree summary
- current file and current selection are attached as `working_set`
- `/api/chat/send` accepts optional structured context
- local folder and local git sessions no longer rely only on the raw user message
- the legacy prompt path still works for backward compatibility
