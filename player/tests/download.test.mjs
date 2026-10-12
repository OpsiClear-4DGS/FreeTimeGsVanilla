import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { downloadFile, downloadJSON } from "../site/download.js";

const size = 4 * 1024 * 1024;
const url = new URL("https://example.test/scene.tsog");
const hash = (data) => createHash("sha256").update(data).digest("hex");

test("range downloads preserve every byte and retry an interrupted chunk without restarting completed chunks", async (t) => {
  const data = new Uint8Array(2 * size + 123);
  for (let i = 0; i < data.length; i++) data[i] = (i * 97 + Math.floor(i / 1024)) & 255;
  const counts = new Map();
  const progress = [];
  t.mock.method(globalThis, "fetch", async (input, { headers }) => {
    assert.equal(String(input), String(url));
    const range = headers.Range;
    const attempt = (counts.get(range) ?? 0) + 1;
    counts.set(range, attempt);
    const [, lo, hi] = /^bytes=(\d+)-(\d+)$/.exec(range);
    const start = Number(lo), end = Number(hi);
    assert.ok(end - start < size, "request exceeds the bounded chunk size");
    if (start === size && attempt === 1) {
      // The headers arrive, but the body stream fails: fetch itself can still resolve.
      const body = new ReadableStream({ start(controller) { controller.error(new TypeError("Failed to fetch")); } });
      return new Response(body, { status: 206, headers: { "Content-Range": `bytes ${start}-${end}/${data.length}` } });
    }
    return new Response(data.slice(start, end + 1), { status: 206, headers: { "Content-Range": `bytes ${start}-${end}/${data.length}` } });
  });
  const blob = await downloadFile(url, { bytes: data.length, onProgress: (value) => progress.push(value) });
  assert.equal(hash(new Uint8Array(await blob.arrayBuffer())), hash(data));
  assert.equal(counts.get(`bytes=0-${size - 1}`), 1);
  assert.equal(counts.get(`bytes=${size}-${2 * size - 1}`), 2);
  assert.equal(progress[0], 0);
  assert.equal(progress.at(-1), 1);
  assert.ok(progress.every((value, i) => !i || value >= progress[i - 1]));
});

test("servers that ignore Range reuse their complete response", async (t) => {
  const data = new Uint8Array(size + 7);
  t.mock.method(globalThis, "fetch", async () => new Response(data));
  const blob = await downloadFile(url, { bytes: data.length });
  assert.equal(blob.size, data.length);
  assert.equal(globalThis.fetch.mock.callCount(), 1);
});

test("download cancellation stops further chunks and keeps the original abort reason", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async () => new Response(new Uint8Array(size), {
    status: 206, headers: { "Content-Range": `bytes 0-${size - 1}/${size + 7}` },
  }));
  await assert.rejects(downloadFile(url, {
    bytes: size + 7, signal: controller.signal,
    onProgress(value) { if (value > 0) controller.abort(); },
  }), { name: "AbortError" });
  assert.equal(globalThis.fetch.mock.callCount(), 1);
});

test("network failures stop after bounded retries and identify the failed URL", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(downloadJSON(url), /Could not fetch https:\/\/example.test\/scene.tsog: Failed to fetch/);
  assert.equal(globalThis.fetch.mock.callCount(), 3);
});

test("permanent HTTP failures are reported without retrying", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 404 }));
  await assert.rejects(downloadJSON(url), /HTTP 404/);
  assert.equal(globalThis.fetch.mock.callCount(), 1);
});

test("incorrect range headers cannot silently corrupt a model", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(new Uint8Array(size), {
    status: 206, headers: { "Content-Range": `bytes 1-${size}/${size + 7}` },
  }));
  await assert.rejects(downloadFile(url, { bytes: size + 7 }), /incorrect byte range/);
  assert.equal(globalThis.fetch.mock.callCount(), 3);
});
