// Optional regeneration only; the player has no dependency on the reference package.
// Usage: node tests/generate-tsog-fixtures.mjs /path/to/built/xiaomi-research/tsog
// Pinned reference and licenses: ../TSOG.md, ../THIRD_PARTY.md.
import { mkdir, open, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { tsogColumns, tsogRow } from "./tsog-source.mjs";
import { openZip } from "../zip.js";

if (!process.argv[2]) throw new Error("Pass a built reference repository path.");
const { writeTsog, loadSplatTransform } = await import(pathToFileURL(resolve(process.argv[2], "dist/index.mjs")));
const { Column, DataTable, WebPCodec, ZipWriter, FileWriter } = await loadSplatTransform();
const output = new URL("./fixtures/tsog/", import.meta.url);
await mkdir(output, { recursive: true });
let seed = 123456789;
Math.random = () => ((seed = (Math.imul(1664525, seed) + 1013904223) >>> 0) / 2 ** 32);
const codec = await WebPCodec.create();
const hashes = {};
async function record(name) {
  const archive = await openZip(new Blob([await readFile(new URL(name, output))]));
  const meta = JSON.parse(new TextDecoder().decode(await archive.read("meta.json")));
  const groups = [meta.means, meta.scales, meta.quats, meta.sh0, meta.shN, meta.timeline, meta.temporal?.means].filter(Boolean);
  hashes[name] = {};
  for (const file of groups.flatMap((group) => group.files)) {
    const decoded = codec.decodeRGBA(await archive.read(file));
    const rgba = decoded.rgba;
    hashes[name][file] = {
      width: decoded.width, height: decoded.height,
      sha256: createHash("sha256").update(rgba).digest("hex"),
    };
  }
}
for (const [name, discrete, bits] of [["continuous8.tsog", false, 8], ["continuous16.tsog", false, 16], ["discrete.tsog", true, 16]]) {
  const table = new DataTable(tsogColumns.map((key) => new Column(key, Float32Array.from({ length: 256 }, (_, i) => tsogRow(i, discrete)[key]))));
  table.tsogSourceMeta = { timelineType: discrete ? 0 : 1, preserveEncodedOrder: true };
  const handle = await open(new URL(name, output), "w");
  try { await writeTsog(handle, table, name, { cpu: true, iterations: 1, bits: { motion: bits }, fps: 24, fpsExplicit: true }); }
  finally { await handle.close(); }
  await record(name);
}
// A static, degree-zero bundle uses the same original-encoder attribute images.
const source = await openZip(new Blob([await readFile(new URL("continuous16.tsog", output))]));
const meta = JSON.parse(new TextDecoder().decode(await source.read("meta.json")));
delete meta.timeline;
delete meta.temporal;
delete meta.shN;
const handle = await open(new URL("static.tsog", output), "w");
try {
  const writer = new ZipWriter(new FileWriter(handle));
  for (const group of [meta.means, meta.scales, meta.quats, meta.sh0])
    for (const file of group.files) await writer.file(file, await source.read(file));
  await writer.file("meta.json", new TextEncoder().encode(JSON.stringify(meta)));
  await writer.close();
} finally { await handle.close(); }
await record("static.tsog");
await writeFile(new URL("image-hashes.json", output), JSON.stringify(hashes, null, 2) + "\n");
