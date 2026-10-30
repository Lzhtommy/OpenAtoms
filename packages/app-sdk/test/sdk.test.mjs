import { test } from "node:test";
import assert from "node:assert/strict";
import { createClient, OAError } from "../dist/index.js";

function fakeFetch(handler) {
  const calls = [];
  const f = async (url, init = {}) => {
    calls.push({ url, init });
    const { status = 200, body = "", headers = {} } = await handler(url, init);
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });
  };
  return { f, calls };
}

test("entities proxy builds REST calls", async () => {
  const { f, calls } = fakeFetch(async () => ({ body: [{ id: 1, title: "a" }] }));
  const oa = createClient({ fetch: f, baseUrl: "http://app.test" });
  const rows = await oa.entities.Todo.filter({ done: false }, { sort: "-created_at", limit: 5 });
  assert.equal(rows[0].title, "a");
  const u = new URL(calls[0].url);
  assert.equal(u.pathname, "/api/v1/entities/Todo");
  assert.equal(u.searchParams.get("query"), '{"done":false}');
  assert.equal(u.searchParams.get("sort"), "-created_at");
  await oa.entities.Todo.update(3, { done: true });
  assert.equal(calls[1].init.method, "PUT");
  assert.match(calls[1].url, /\/entities\/Todo\/3$/);
});

test("auth stores token and sends bearer", async () => {
  const { f, calls } = fakeFetch(async (url) =>
    url.endsWith("/auth/login") ? { body: { token: "tok", user: { id: 1, email: "a@b.c" } } } : { body: { id: 1, email: "a@b.c" } },
  );
  const oa = createClient({ fetch: f, tokenKey: "t1" });
  await oa.auth.login("a@b.c", "pw");
  assert.equal(oa.auth.getToken(), "tok");
  const me = await oa.auth.me();
  assert.equal(me.email, "a@b.c");
  assert.equal(calls[1].init.headers.Authorization, "Bearer tok");
});

test("errors raise OAError", async () => {
  const { f } = fakeFetch(async () => ({ status: 404, body: { detail: "nope" } }));
  const oa = createClient({ fetch: f });
  await assert.rejects(oa.entities.X.get(1), (e) => e instanceof OAError && e.status === 404 && e.message === "nope");
});

test("gentxt parses SSE stream", async () => {
  const { f } = fakeFetch(async () => ({
    body: 'data: {"content":"Hel"}\n\ndata: {"content":"lo"}\n\ndata: [DONE]\n\n',
    headers: { "content-type": "text/event-stream" },
  }));
  const oa = createClient({ fetch: f });
  const chunks = [];
  const text = await oa.ai.gentxt({ messages: [{ role: "user", content: "hi" }], onChunk: (c) => chunks.push(c) });
  assert.equal(text, "Hello");
  assert.deepEqual(chunks, ["Hel", "lo"]);
});
