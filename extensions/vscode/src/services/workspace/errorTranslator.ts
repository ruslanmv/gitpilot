/**
 * GitPilot Redesign — Error Translator
 * Converts raw exceptions to user-friendly messages.
 */

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
      match: (err) => err?.status === 500 || err?.statusCode === 500,
      message: "Server error. Please check that the GitPilot backend is running correctly.",
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
