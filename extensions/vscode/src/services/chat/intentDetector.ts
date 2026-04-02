/**
 * GitPilot — Intent Detection Layer
 *
 * Infers user intent from natural language so users never need slash commands.
 * Slash commands (/refactor, /fix, etc.) still work as explicit overrides
 * for power users, but the primary UX is natural language.
 */

export type ChatIntent =
  | "fix"
  | "refactor"
  | "explain"
  | "test"
  | "review"
  | "optimize"
  | "security"
  | "general";

interface IntentResult {
  intent: ChatIntent;
  /** The user message with any leading slash command stripped. */
  cleanMessage: string;
  /** True if intent came from an explicit /command. */
  explicit: boolean;
}

// Slash command → intent mapping (power-user override)
const SLASH_COMMANDS: Record<string, ChatIntent> = {
  "/fix": "fix",
  "/debug": "fix",
  "/refactor": "refactor",
  "/clean": "refactor",
  "/improve": "refactor",
  "/explain": "explain",
  "/what": "explain",
  "/test": "test",
  "/tests": "test",
  "/review": "review",
  "/optimize": "optimize",
  "/perf": "optimize",
  "/security": "security",
  "/scan": "security",
};

// Keyword patterns for natural-language detection (order = priority)
const INTENT_PATTERNS: Array<{ intent: ChatIntent; patterns: RegExp[] }> = [
  {
    intent: "fix",
    patterns: [
      /\b(fix|bug|error|broken|crash|fail|issue|wrong|not working|doesn'?t work|exception|traceback)\b/i,
    ],
  },
  {
    intent: "test",
    patterns: [
      /\b(test|tests|unit test|spec|coverage|assert|mock)\b/i,
      /\bgenerate\s+tests?\b/i,
      /\bwrite\s+tests?\b/i,
    ],
  },
  {
    intent: "security",
    patterns: [
      /\b(security|vulnerab|injection|xss|csrf|auth\s*bypass|owasp|cve)\b/i,
      /\bsecurity\s+scan\b/i,
    ],
  },
  {
    intent: "explain",
    patterns: [
      /\b(explain|what does|how does|what is|why does|walk me through|describe|understand)\b/i,
    ],
  },
  {
    intent: "review",
    patterns: [
      /\b(review|code review|check\s+this|look at|feedback|suggestions?)\b/i,
    ],
  },
  {
    intent: "optimize",
    patterns: [
      /\b(optimi[sz]e|performance|slow|fast|speed|efficien|reduce|memor[yie])\b/i,
    ],
  },
  {
    intent: "refactor",
    patterns: [
      /\b(refactor|clean|simplif|restructur|reorgani[sz]|improve|readable|maintain|extract|too complex|messy|ugly)\b/i,
    ],
  },
];

/**
 * Detect intent from a user message.
 *
 * Priority:
 *   1. Explicit slash command (e.g. "/fix this bug")
 *   2. Keyword-based natural language detection
 *   3. Fallback to "general"
 */
export function detectIntent(message: string): IntentResult {
  const trimmed = message.trim();

  // 1) Explicit slash command
  if (trimmed.startsWith("/")) {
    const firstSpace = trimmed.indexOf(" ");
    const cmd = (firstSpace > 0 ? trimmed.substring(0, firstSpace) : trimmed).toLowerCase();
    const intent = SLASH_COMMANDS[cmd];
    if (intent) {
      return {
        intent,
        cleanMessage: firstSpace > 0 ? trimmed.substring(firstSpace + 1).trim() : "",
        explicit: true,
      };
    }
    // Unknown slash command → treat as general, pass through as-is
  }

  // 2) Natural language detection
  for (const { intent, patterns } of INTENT_PATTERNS) {
    for (const pattern of patterns) {
      if (pattern.test(trimmed)) {
        return { intent, cleanMessage: trimmed, explicit: false };
      }
    }
  }

  // 3) Fallback
  return { intent: "general", cleanMessage: trimmed, explicit: false };
}

/**
 * Build a system-level prompt prefix based on detected intent.
 * This gives the LLM a clear task framing without the user having to specify it.
 */
export function buildIntentPrefix(intent: ChatIntent): string {
  switch (intent) {
    case "fix":
      return "The user wants to fix a bug or error. Diagnose the issue and provide a corrected implementation.";
    case "refactor":
      return "The user wants to refactor or improve code quality. Simplify, restructure, and improve readability.";
    case "explain":
      return "The user wants an explanation. Provide a clear, concise walkthrough of the code or concept.";
    case "test":
      return "The user wants tests generated. Write comprehensive unit tests with good coverage.";
    case "review":
      return "The user wants a code review. Identify issues, suggest improvements, and highlight good patterns.";
    case "optimize":
      return "The user wants performance optimization. Identify bottlenecks and provide faster alternatives.";
    case "security":
      return "The user wants a security analysis. Check for vulnerabilities and suggest secure alternatives.";
    case "general":
      return "";
  }
}
