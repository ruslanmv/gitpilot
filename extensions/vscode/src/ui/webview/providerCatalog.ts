/**
 * Static facts about each LLM provider: what to call it, what it needs, and
 * where a user goes to get a key. Kept out of the panel so the host and the
 * webview template agree on one description of the world.
 */
import { OllaBridgeMode, ProviderName } from "../../core/types";

/** OllaBridge Cloud's hosted gateway — the zero-setup default. */
export const OLLABRIDGE_CLOUD_URL = "https://ruslanmv-ollabridge.hf.space";

/**
 * A self-hosted OllaBridge gateway. Note the port: 11435, *not* 8000.
 * Port 8000 is GitPilot's own backend, and pointing OllaBridge at it makes
 * model discovery ask GitPilot about itself.
 */
export const OLLABRIDGE_LOCAL_URL = "http://127.0.0.1:11435";

/** Where a user completes browser sign-in and receives a pairing code. */
export const OLLABRIDGE_LINK_URL = "https://app.ollabridge.com/link";

/** Ollama's default listen address. */
export const OLLAMA_LOCAL_URL = "http://127.0.0.1:11434";

/** Open WebUI's default listen address. */
export const OPENWEBUI_LOCAL_URL = "http://localhost:3000";

export interface ProviderMeta {
  name: ProviderName;
  label: string;
  /** One line, shown on the overview list. */
  description: string;
  /** Whether an API key is required for the provider to work at all. */
  requiresApiKey: boolean;
  /** Where to obtain credentials, shown as a link on the provider page. */
  keyUrl?: string;
  keyUrlLabel?: string;
  /** Placeholder for the base URL field. */
  defaultBaseUrl?: string;
  /** Models offered when live discovery is unavailable. */
  suggestedModels: string[];
}

export const PROVIDER_CATALOG: Record<ProviderName, ProviderMeta> = {
  ollabridge: {
    name: "ollabridge",
    label: "OllaBridge Cloud",
    description: "Free hosted models — sign in, no API key needed",
    requiresApiKey: false,
    keyUrl: OLLABRIDGE_LINK_URL,
    keyUrlLabel: "app.ollabridge.com",
    defaultBaseUrl: OLLABRIDGE_CLOUD_URL,
    suggestedModels: ["qwen2.5:1.5b", "llama3.2", "qwen2.5-coder:7b"],
  },
  ollama: {
    name: "ollama",
    label: "Ollama (Local)",
    description: "Run models locally on your computer",
    requiresApiKey: false,
    keyUrl: "https://ollama.com/download",
    keyUrlLabel: "ollama.com",
    defaultBaseUrl: OLLAMA_LOCAL_URL,
    suggestedModels: ["qwen2.5-coder:7b", "llama3.2", "codellama", "mistral"],
  },
  claude: {
    name: "claude",
    label: "Claude (Anthropic)",
    description: "Anthropic's Claude models — requires an Anthropic API key",
    requiresApiKey: true,
    keyUrl: "https://console.anthropic.com/settings/keys",
    keyUrlLabel: "console.anthropic.com",
    defaultBaseUrl: "https://api.anthropic.com",
    suggestedModels: [
      "claude-sonnet-4-5",
      "claude-opus-4-6",
      "claude-haiku-4-5-20251001",
    ],
  },
  openai: {
    name: "openai",
    label: "OpenAI",
    description: "GPT models — requires an OpenAI API key",
    requiresApiKey: true,
    keyUrl: "https://platform.openai.com/api-keys",
    keyUrlLabel: "platform.openai.com",
    defaultBaseUrl: "https://api.openai.com/v1",
    suggestedModels: ["gpt-4o", "gpt-4o-mini", "o1-mini"],
  },
  watsonx: {
    name: "watsonx",
    label: "IBM watsonx",
    description: "IBM Granite and foundation models — API key and project ID",
    requiresApiKey: true,
    keyUrl: "https://cloud.ibm.com/iam/apikeys",
    keyUrlLabel: "cloud.ibm.com",
    defaultBaseUrl: "https://us-south.ml.cloud.ibm.com",
    suggestedModels: [
      "ibm/granite-3-8b-instruct",
      "meta-llama/llama-3-3-70b-instruct",
    ],
  },
  openwebui: {
    name: "openwebui",
    label: "Open WebUI",
    description: "Connect to your Open WebUI instance and use its models",
    requiresApiKey: false,
    keyUrl: "https://docs.openwebui.com/getting-started/api-endpoints/",
    keyUrlLabel: "docs.openwebui.com",
    defaultBaseUrl: OPENWEBUI_LOCAL_URL,
    suggestedModels: [],
  },
  custom: {
    name: "custom",
    label: "Custom endpoint",
    description: "Any OpenAI-compatible gateway — URL, key and headers",
    requiresApiKey: false,
    defaultBaseUrl: "https://inference.example.com/v1",
    suggestedModels: [],
  },
};

/** Overview order: the zero-friction options first, the escape hatch last. */
export const PROVIDER_ORDER: ProviderName[] = [
  "ollabridge",
  "ollama",
  "claude",
  "openai",
  "watsonx",
  "openwebui",
  "custom",
];

/**
 * Infer which OllaBridge tab a stored configuration belongs to.
 *
 * The backend stores one flat config, so the mode has to be read back out of
 * it: a loopback URL is a local gateway, a key against the cloud is API-key
 * mode, and anything else is a cloud session.
 */
export function inferOllaBridgeMode(
  baseUrl: string | undefined,
  hasApiKey: boolean
): OllaBridgeMode {
  const url = (baseUrl || "").trim();
  if (!url) {
    return "cloud";
  }
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host === "localhost" || host === "127.0.0.1" || host === "::1") {
      return "local";
    }
  } catch {
    /* an unparseable URL is not a local gateway */
  }
  return hasApiKey ? "api_key" : "cloud";
}
