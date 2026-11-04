import { test } from "node:test";
import assert from "node:assert/strict";

import { initialDevicePollInterval, nextDevicePoll } from "../utils/devicePolling.js";

test("first poll uses GitHub's interval, or its five-second default", () => {
  assert.equal(initialDevicePollInterval({ interval: 9 }), 9);
  assert.equal(initialDevicePollInterval({ interval: "6" }), 6);
  assert.equal(initialDevicePollInterval({}), 5);
  assert.equal(initialDevicePollInterval({ interval: 0 }), 5);
  assert.equal(initialDevicePollInterval({ interval: "bad" }), 5);
});

test("pending device authorization waits for the advertised interval", () => {
  assert.deepEqual(nextDevicePoll(202, { status: "pending" }, 7, 2),
    { interval: 7, failures: 0 });
});

test("GitHub slow_down adds five seconds and honors the upstream interval", () => {
  assert.deepEqual(nextDevicePoll(429, { error: "slow_down" }, 5),
    { interval: 10, failures: 0 });
  assert.deepEqual(nextDevicePoll(429, { error: "slow_down", interval: 18 }, 10),
    { interval: 18, failures: 0 });
  assert.deepEqual(nextDevicePoll(429, { error: "slow_down", interval: 3 }, 10),
    { interval: 15, failures: 0 });
});

test("upstream and network outages are retried with bounded backoff", () => {
  assert.deepEqual(nextDevicePoll(503, {}, 5, 0), { interval: 10, failures: 1 });
  assert.deepEqual(nextDevicePoll(0, {}, 10, 1), { interval: 20, failures: 2 });
  assert.deepEqual(nextDevicePoll(500, {}, 20, 2), { interval: 30, failures: 3 });
  assert.equal(nextDevicePoll(503, {}, 30, 3), null);
});

test("invalid authorization and failed session storage are terminal", () => {
  assert.equal(nextDevicePoll(400, { error: "access_denied" }, 5), null);
  assert.equal(nextDevicePoll(503, { code: "session_storage_unavailable" }, 5), null);
});
