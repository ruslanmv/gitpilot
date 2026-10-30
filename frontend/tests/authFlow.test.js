// frontend/tests/authFlow.test.js — what the /auth page does with a URL.
//
// An OAuth return is only trusted when its `state` matches the one this tab
// stored before leaving, and is fresh; anything else is refused before the
// code reaches the server (login-CSRF defence on top of the server's check).
import { test } from "node:test";
import assert from "node:assert/strict";

import { PENDING_TTL_MS, errorText, initialView } from "../utils/authFlow.js";

const at = (pathname, search) => ({ pathname, search });
const NOW = 1_700_000_000_000;

test("a callback whose state matches this tab's pending sign-in is trusted", () => {
  const pending = { provider: "gitlab", state: "s1", startedAt: NOW - 1000 };
  assert.deepEqual(initialView(at("/auth", "?code=c&state=s1"), pending, NOW),
    { view: "callback", provider: "gitlab", trusted: true });
});

test("a callback with someone else's state is not trusted", () => {
  const pending = { provider: "github", state: "mine", startedAt: NOW };
  assert.deepEqual(initialView(at("/auth", "?code=c&state=theirs"), pending, NOW),
    { view: "callback", provider: null, trusted: false });
  assert.equal(initialView(at("/auth", "?code=c&state=x"), null, NOW).trusted, false);
});

test("a stale pending sign-in is not trusted", () => {
  const pending = { provider: "github", state: "s", startedAt: NOW - PENDING_TTL_MS - 1 };
  assert.equal(initialView(at("/auth", "?code=c&state=s"), pending, NOW).trusted, false);
});

test("a provider's error redirect is handled as a callback", () => {
  const pending = { provider: "gitlab", state: "s", startedAt: NOW };
  const v = initialView(at("/auth", "?error=access_denied&state=s"), pending, NOW);
  assert.equal(v.view, "callback");
  assert.equal(v.trusted, true);
});

test("email links and provider shortcuts", () => {
  assert.deepEqual(initialView(at("/auth/verify-email", "?token=t"), null), { view: "verify" });
  assert.deepEqual(initialView(at("/auth/reset-password", "?token=t"), null), { view: "reset" });
  assert.deepEqual(initialView(at("/auth", "?connect=gitlab&view=gitlab"), null), { view: "provider", provider: "gitlab" });
  assert.deepEqual(initialView(at("/auth", "?mode=signup"), null), { view: "email", mode: "signup" });
  assert.deepEqual(initialView(at("/auth", ""), null), { view: "email", mode: "signin" });
});

test("server error bodies become readable messages", () => {
  assert.equal(errorText({ detail: "Expired" }, "x"), "Expired");
  assert.equal(errorText({ detail: [{ msg: "field required" }] }, "x"), "field required");
  assert.equal(errorText({}, "fallback"), "fallback");
});
