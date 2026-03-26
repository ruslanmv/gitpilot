/**
 * GitPilot Redesign — Constants
 */

export const EXTENSION_ID = "gitpilot";

export const DEFAULT_SERVER_URL = "http://127.0.0.1:8000";

export const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  claude: "Claude (Anthropic)",
  watsonx: "Watsonx (IBM)",
  ollama: "Ollama",
  ollabridge: "OllaBridge",
};

export const PROVIDER_DESCRIPTIONS: Record<string, string> = {
  openai: "SaaS LLM via OpenAI API",
  claude: "Anthropic Claude models",
  watsonx: "Enterprise, private cloud",
  ollama: "Fully offline, local models",
  ollabridge: "Local or hosted bridge",
};

export const WORKSPACE_MODE_LABELS: Record<string, string> = {
  folder: "Folder Mode",
  local_git: "Local Git Mode",
  github: "GitHub Mode",
};

export const WORKSPACE_MODE_DESCRIPTIONS: Record<string, string> = {
  folder: "Use local files only",
  local_git: "Use repo + branch context",
  github: "Use PRs, issues, remote workflows",
};

export const QUICK_ACTIONS = [
  { id: "explain_project", label: "Explain this project", icon: "book" },
  { id: "review_file", label: "Review current file", icon: "eye" },
  { id: "fix_selection", label: "Fix selected code", icon: "wrench" },
  { id: "generate_tests", label: "Generate tests", icon: "beaker" },
  { id: "security_scan", label: "Run security scan", icon: "shield" },
] as const;

export const HEALTH_CHECK_INTERVAL_MS = 30_000;
export const HEALTH_CHECK_TIMEOUT_MS = 5_000;
