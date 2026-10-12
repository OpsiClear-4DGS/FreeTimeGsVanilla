import { test } from "node:test";
import assert from "node:assert/strict";
import { downloadExample } from "../site/examples.js";

function setOrigin(t, value) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "origin");
  Object.defineProperty(globalThis, "origin", { value, writable: true, configurable: true });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "origin", previous);
    else delete globalThis.origin;
  });
}

test("opaque sandbox and remote examples do not depend on CORS-hidden range headers", async (t) => {
  const data = new Uint8Array(4 * 1024 * 1024 + 7);
  const example = { source: new URL("https://example.test/scene.tsog"), bytes: data.length };
  t.mock.method(globalThis, "fetch", async (_input, { headers }) => {
    assert.equal(headers, undefined, "Cross-origin example sent a byte-range request");
    return new Response(data);
  });
  setOrigin(t, "null");
  for (const origin of ["null", "https://other.test"]) {
    globalThis.origin = origin;
    assert.equal((await downloadExample(example)).size, data.length);
  }
  assert.equal(globalThis.fetch.mock.callCount(), 2);
});

test("remote example downloads still reject truncated model bodies", async (t) => {
  setOrigin(t, "null");
  t.mock.method(globalThis, "fetch", async () => new Response(new Uint8Array(2)));
  await assert.rejects(downloadExample({
    source: new URL("https://example.test/scene.tsog"), bytes: 7,
  }), /Incomplete example download/);
});
