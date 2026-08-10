/**
 * GitPilot Redesign — Error Translator
 * Converts raw exceptions to user-friendly messages.
 *
 * The canned message per status code is a floor, not a ceiling. When the
 * backend sends a `detail`, that text wins: it was written for this exact
 * failure and names the provider, the missing package and the command to
 * run, none of which can be recovered from a status number. Guessing over
 * it is how a configured Ollama came to report a "circuit breaker" that
 * was never involved.
 */

/** Codes whose canned text is a guess we should not print over a real one. */
const _detailedStatuses = new Set([500, 502, 503, 504]);

/** Read the server's explanation off an error, if it carried one. */
function _serverDetail(err: any): string {
  const detail = err?.detail ?? err?.response?.data?.detail;
  return typeof detail === "string" ? detail.trim() : "";
}

export class ErrorTranslator {
  private _patterns: Array<{
    match: (err: any) => boolean;
    message: string;
  }> = [
    {
      match: (err) => err?.status === 422 || err?.statusCode === 422,
      message:
        "GitPilot could not start a session because the workspace data is incomplete.",
    },
    {
      match: (err) => err?.status === 401 || err?.statusCode === 401,
      message: "Authentication required. Please check your API key or token.",
    },
    {
      match: (err) => err?.status === 403 || err?.statusCode === 403,
      message: "Access denied. You may not have permission for this action.",
    },
    {
      match: (err) => err?.status === 404 || err?.statusCode === 404,
      message: "Resource not found. The session or endpoint may have expired.",
    },
    {
      match: (err) => err?.status === 429 || err?.statusCode === 429,
      message:
        "LLM provider rate limit or quota exceeded. Check your plan and billing, or switch to a different provider in Settings.",
    },
    {
      match: (err) => err?.status === 500 || err?.statusCode === 500,
      message: "Server error. Please check that the GitPilot backend is running correctly.",
    },
    {
      match: (err) => err?.status === 502 || err?.statusCode === 502,
      message:
        "The LLM returned an empty response. Try enabling Lite Mode in Settings for better results with small models.",
    },
    {
      match: (err) => err?.status === 503 || err?.statusCode === 503,
      // Deliberately vague: 503 covers both a tripped circuit breaker and an
      // agent runtime that could not be built, and only the server knows
      // which. When it says, `_serverDetail` speaks instead of this.
      message:
        "The LLM provider is unavailable. Check Settings → AI Providers, or wait a moment and try again.",
    },
    {
      match: (err) => err?.status === 504 || err?.statusCode === 504,
      message:
        "The agent operation timed out. The LLM provider may be overloaded — try again or switch to a faster provider in Settings.",
    },
    {
      match: (err) =>
        err?.code === "ECONNREFUSED" || err?.message?.includes("ECONNREFUSED"),
      message:
        "Cannot connect to GitPilot server. Make sure the backend is running.",
    },
    {
      match: (err) =>
        err?.code === "ETIMEDOUT" || err?.message?.includes("timeout"),
      message: "Request timed out. The server may be overloaded.",
    },
  ];

  translate(err: any): string {
    if (!err) {
      return "An unknown error occurred.";
    }

    // The server's own words, for the codes where the canned line is a guess.
    const status = err?.status ?? err?.statusCode;
    const detail = _serverDetail(err);
    if (detail && _detailedStatuses.has(status)) {
      return detail;
    }

    for (const pattern of this._patterns) {
      if (pattern.match(err)) {
        return pattern.message;
      }
    }

    // Fallback: use error message if available
    if (typeof err === "string") {
      return err;
    }
    if (err.message) {
      return `Error: ${err.message}`;
    }
    if (err.detail) {
      return `Error: ${err.detail}`;
    }

    return "An unexpected error occurred. Please try again.";
  }
}
