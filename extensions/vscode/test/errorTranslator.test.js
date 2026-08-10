/**
 * What the extension says when the backend says something better.
 *
 * The report these come from: a correctly configured, reachable Ollama, and
 * VS Code answering every task with
 *
 *     The LLM provider is temporarily unavailable (circuit breaker active).
 *
 * No circuit breaker was involved. The backend had sent a 503 whose body
 * named the provider, the missing package and the command to run — and the
 * translator threw it away in favour of a sentence chosen from the status
 * code alone. A guess printed over the truth is worse than no message: it
 * sent the user looking for a breaker that does not exist.
 *
 * So: the canned line per status is a floor. When the server explains
 * itself, the server wins.
 *
 * Run with:  npm test        (from extensions/vscode)
 */
const assert = require("assert");
const path = require("path");

const { ErrorTranslator } = require(
  path.resolve(__dirname, "../out/services/workspace/errorTranslator.js")
);

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const translator = new ErrorTranslator();

/** The 503 the backend now sends when it cannot build the agent runtime. */
const RUNTIME_DETAIL =
  "GitPilot could not reach ollama through the agent runtime. This endpoint " +
  "speaks the OpenAI chat API and needs no LiteLLM, so the installed CrewAI " +
  "is too old to route it natively. Upgrade with: pip install -U " +
  "'crewai>=1.6' gitcopilot";

test("a 503 that explains itself is not overwritten by the canned line", () => {
  const message = translator.translate({ status: 503, detail: RUNTIME_DETAIL });

  assert.strictEqual(message, RUNTIME_DETAIL);
  assert.doesNotMatch(
    message,
    /circuit breaker/i,
    "the breaker was never involved; saying so cost the reporter a debugging session"
  );
});

test("the canned line still covers a 503 that says nothing", () => {
  const message = translator.translate({ status: 503 });

  assert.match(message, /unavailable/i, "a bare 503 still needs an answer");
  assert.doesNotMatch(
    message,
    /circuit breaker/i,
    "503 covers a tripped breaker and a runtime that would not build — " +
      "claiming to know which, without being told, is the original bug"
  );
});

for (const status of [500, 502, 504]) {
  test(`a ${status} carrying a reason reports the reason`, () => {
    const detail = `the reason the server gave for ${status}`;
    assert.strictEqual(translator.translate({ status, detail }), detail);
  });
}

test("statusCode is read as well as status", () => {
  // The client sets `status`; other call sites in this codebase set
  // `statusCode`. Both spellings reach here, so both have to work.
  assert.strictEqual(
    translator.translate({ statusCode: 503, detail: RUNTIME_DETAIL }),
    RUNTIME_DETAIL
  );
});

test("an axios-shaped error is unwrapped too", () => {
  assert.strictEqual(
    translator.translate({ status: 503, response: { data: { detail: RUNTIME_DETAIL } } }),
    RUNTIME_DETAIL
  );
});

test("a detail on a code with a precise message does not displace it", () => {
  // 401 already says the one useful thing, and the server's detail for it is
  // typically a bare "Not authenticated". Only the vague codes defer.
  const message = translator.translate({ status: 401, detail: "Not authenticated" });

  assert.match(message, /API key or token/, "the actionable line survives");
});

test("whitespace-only detail counts as no detail", () => {
  const message = translator.translate({ status: 503, detail: "   " });

  assert.match(message, /unavailable/i, "an empty explanation is not an explanation");
});

test("an error with no status at all still translates", () => {
  assert.strictEqual(translator.translate(null), "An unknown error occurred.");
  assert.match(translator.translate({ message: "boom" }), /boom/);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`  ✓ ${name}`);
    } catch (err) {
      failed++;
      console.log(`  ✗ ${name}\n      ${err.message}`);
    }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
