// frontend/tests/backend.test.js — which backend a build talks to.
import { test } from "node:test";
import assert from "node:assert/strict";

import { HOSTED_BACKEND_URL, backendUrlForHost } from "../utils/backend.js";

test("an explicit VITE_BACKEND_URL always wins", () => {
  assert.equal(backendUrlForHost("gitpilot.ruslanmv.com", "https://api.example.com/"), "https://api.example.com");
});

test("local development and Hugging Face Spaces use their own origin", () => {
  assert.equal(backendUrlForHost("localhost"), "");
  assert.equal(backendUrlForHost("ruslanmv-gitpilot.hf.space"), "");
  assert.equal(backendUrlForHost("someone-gitpilot-copy.hf.space"), ""); // a duplicated Space
});

test("the production site uses the hosted backend", () => {
  assert.equal(backendUrlForHost("gitpilot.ruslanmv.com"), HOSTED_BACKEND_URL);
  assert.equal(HOSTED_BACKEND_URL, "https://ruslanmv-gitpilot.hf.space");
});
