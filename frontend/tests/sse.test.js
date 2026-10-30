// frontend/tests/sse.test.js — Batch V4-H4
//
// The SSE client against a scripted fetch: chunk boundaries that split an
// event, framing lines around the data, the usage of the typed handlers, and
// the two cancellations (local abort, backend cancel).
import { test } from "node:test";
import assert from "node:assert/strict";

import { cancelRun, dispatchEvent, parseSseBlock, respondToApproval, streamChat } from "../utils/sse.js";

function streamResponse(chunks, { ok = true, status = 200 } = {}) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok, status, body };
}

function scriptedFetch(response, calls = []) {
  return async (url, init) => {
    calls.push({ url: String(url), init });
    return response;
  };
}

function streamToEnd(sessionId, message, handlers, options) {
  return new Promise((resolve, reject) => {
    const events = [];
    streamChat(
      sessionId,
      message,
      {
        ...handlers,
        onEvent: (event) => {
          events.push(event);
          handlers?.onEvent?.(event);
        },
        onDone: (event) => {
          handlers?.onDone?.(event);
          resolve(events);
        },
        onError: (error) => {
          handlers?.onError?.(error);
          if (error?.type === "error" && !error?.recoverable) resolve(events);
          else reject(error);
        },
      },
      options,
    );
  });
}

test("parseSseBlock reads data lines and ignores framing", () => {
  assert.deepEqual(parseSseBlock('id: 7\nevent: message\ndata: {"type":"done"}'), { type: "done" });
  assert.deepEqual(parseSseBlock('data: {"type":"text_delta",\ndata:  "text":"x"}'), { type: "text_delta", text: "x" });
  assert.equal(parseSseBlock("data: [DONE]"), null);
  assert.equal(parseSseBlock(": keepalive"), null);
  assert.equal(parseSseBlock("data: {not json"), null);
});

test("events split across chunks arrive whole and in order", async () => {
  const calls = [];
  const response = streamResponse([
    'data: {"type":"status_change","status":"planning","message":"Analyzing..."}\n\n',
    'data: {"type":"text_del',
    'ta","text":"Hel"}\n\ndata: {"type":"text_delta","text":"lo"}\n\n',
    'id: 3\ndata: {"type":"tool_start","tool_id":"t1","name":"fs.read","args":{"path":"a"}}\n\n',
    'data: {"type":"done","usage":{"prompt_tokens":1},"summary":"Hello"}\n\n',
  ]);
  const text = [];
  const events = await streamToEnd(
    "s1",
    "hi",
    { onTextDelta: (t) => text.push(t) },
    { fetch: scriptedFetch(response, calls), headers: { Authorization: "Bearer x" }, token: "ghp_test" },
  );

  assert.deepEqual(events.map((e) => e.type), ["status_change", "text_delta", "text_delta", "tool_start", "done"]);
  assert.deepEqual(text, ["Hel", "lo"]);

  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/api\/v2\/chat\/stream$/);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.Authorization, "Bearer x");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    session_id: "s1", message: "hi", permission_mode: "normal", token: "ghp_test",
  });
});

test("a final block without a trailing blank line is still delivered", async () => {
  const response = streamResponse(['data: {"type":"text_delta","text":"a"}\n\ndata: {"type":"done","summary":""}']);
  const events = await streamToEnd("s2", "hi", {}, { fetch: scriptedFetch(response) });
  assert.deepEqual(events.map((e) => e.type), ["text_delta", "done"]);
});

test("a non-2xx answer is reported through onError, not thrown", async () => {
  const errors = [];
  await new Promise((resolve) => {
    streamChat("s3", "hi", { onError: (e) => { errors.push(e); resolve(); } }, {
      fetch: scriptedFetch(streamResponse([], { ok: false, status: 503 })),
    });
  });
  assert.deepEqual(errors, [{ type: "error", error: "Server returned 503" }]);
});

test("aborting the stream is silent", async () => {
  const fired = [];
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const pendingFetch = async (_url, init) => {
    await gate;
    init.signal.throwIfAborted();
    return streamResponse([]);
  };
  const stop = streamChat("s4", "hi", { onError: (e) => fired.push(e), onDone: (e) => fired.push(e) }, { fetch: pendingFetch });
  stop();
  release();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(fired, []);
});

test("cancelRun and respondToApproval post to the v2 endpoints", async () => {
  const calls = [];
  const okFetch = scriptedFetch({ ok: true, status: 200 }, calls);

  assert.equal(await cancelRun("s5", { fetch: okFetch, headers: { Authorization: "Bearer y" } }), true);
  assert.equal(await respondToApproval("s5", "approval-9", true, "session", { fetch: okFetch }), true);

  assert.match(calls[0].url, /\/api\/v2\/agent\/cancel$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { session_id: "s5" });
  assert.equal(calls[0].init.headers.Authorization, "Bearer y");
  assert.match(calls[1].url, /\/api\/v2\/approval\/respond$/);
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    session_id: "s5", request_id: "approval-9", approved: true, scope: "session",
  });
});

test("dispatchEvent fans out to the typed handlers after onEvent", () => {
  const order = [];
  dispatchEvent(
    { type: "approval_needed", request_id: "a1" },
    { onEvent: () => order.push("event"), onApprovalNeeded: () => order.push("typed") },
  );
  dispatchEvent({ type: "status_change", status: "done", message: "" }, { onStatusChange: (s) => order.push(s) });
  dispatchEvent(null, { onEvent: () => order.push("never") });
  assert.deepEqual(order, ["event", "typed", "done"]);
});
