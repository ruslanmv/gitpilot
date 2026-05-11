# GitPilot documentation

**Open-source multi-agent AI coding assistant.**
Plan, code, test, and ship — with you in the loop.

## Get started — three commands

```bash
pip install gitcopilot
GITPILOT_FLAGS="init_wizard=1" gitpilot init --wizard
gitpilot serve
```

Open [http://localhost:8000](http://localhost:8000).

## Sections

* **[Quickstart](quickstart.md)** — install, configure a model, run the
  first chat.
* **[API stability contract](API_STABILITY.md)** — what
  `gitpilot.public_api` promises, deprecation policy, SemVer mapping.
* **[Deploy](deploy/)** — Docker, Render, Vercel, MCP stack, production.
* **[Contributing](contributing/packaging.md)** — packaging, frontend
  reference, hacking on GitPilot itself.
* **Phase history** — [Phase 1](PHASE1.md), [Phase 2](PHASE2.md),
  [Phase 3-G](PHASE3_G.md).
* **[Upgrade catalogue](UPGRADES.md)** — every feature introduced via
  the Phase plan.

## Why GitPilot?

* **Four agents, not one.**  Explorer reads, Planner drafts, Coder
  writes, Reviewer audits.  You see every step.
* **Any LLM.**  Anthropic, OpenAI, watsonx, Ollama.  Switch in
  settings, no code change.
* **Safe by default.**  Sandboxed shell, file-regex edit guards,
  atomic checkpoints, trusted-folder gate.
* **Daily-driver speed.**  Prompt cache, lazy tool defs, context-pack
  LRU, SSE streaming, model warmup — every one flag-gated.
* **Stable contract.**  Build on `gitpilot.public_api` and stay
  unbroken through major bumps.

## License

Apache 2.0.
