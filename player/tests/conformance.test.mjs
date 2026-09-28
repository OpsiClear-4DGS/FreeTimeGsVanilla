import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { decodeTSOG, validateTSOGMetadata, TSOG_PROFILE } from "../tsog.js";
import { resolvePlayback } from "../playback.js";
import { openZip } from "../zip.js";
import { inspectAttributeImage } from "../webp.js";
import { packageTSOG } from "../tsog-package.js";
import { validateTSOGPackage } from "../tsog-validate.js";
import { toneFile } from "./audio-fixture.mjs";

const fixture = (name) => new URL(`./fixtures/tsog/${name}`, import.meta.url);
const json = async (name) => JSON.parse(await readFile(fixture(name), "utf8"));
const source = async (name = "continuous16.tsog") => new Blob([await readFile(fixture(name))]);

test("published metadata cases match runtime acceptance, including semantic-only failures", async () => {
  const { base, cases } = await json("metadata-cases.json");
  for (const item of cases) {
    const meta = structuredClone(base);
    for (const [path, value] of item.edits) {
      const keys = path.split("."), last = keys.pop();
      keys.reduce((target, key) => target[key], meta)[last] = value;
    }
    if (item.valid) assert.doesNotThrow(() => validateTSOGMetadata(meta), item.name);
    else assert.throws(() => validateTSOGMetadata(meta), /Invalid TSOG/, item.name);
  }
  assert.throws(() => validateTSOGMetadata(base, { requireProfile: true }), /explicit/);
});

test("independent analytic vector fixes byte order, channel order, SH, motion and media timing", async () => {
  const { meta, images, expected, tolerance } = await json("decode-vector.json");
  const load = async (name) => {
    const { width, height, pixels, fill } = images[name];
    return { width, height, rgba: Uint8Array.from((pixels ?? Array(width * height).fill(fill)).flat()) };
  };
  const model = await decodeTSOG(meta, load, { maxPoints: Infinity });
  const near = (values, wanted, label) => {
    assert.equal(values.length, wanted.length, label);
    values.forEach((value, i) => assert.ok(Math.abs(value - wanted[i]) <= tolerance, `${label}[${i}]: ${value} != ${wanted[i]}`));
  };
  for (const name of ["positionTime", "velocityDuration", "covarianceA", "alpha", "sh"])
    near([...model[name]], expected[name], name);
  const positions = [], opacity = [];
  for (let i = 0; i < meta.count; i++) {
    const dt = -model.positionTime[i * 4 + 3];
    for (let a = 0; a < 3; a++) positions.push(model.positionTime[i * 4 + a] + model.velocityDuration[i * 4 + a] * dt);
    opacity.push(model.alpha[i] * Math.exp(-0.5 * (dt / model.velocityDuration[i * 4 + 3]) ** 2));
  }
  near(positions, expected.positionsAtTimeZero, "position(t=0)");
  near(opacity, expected.opacityAtTimeZero, "opacity(t=0)");
  assert.deepEqual(resolvePlayback(model), expected.playback);
});

test("legacy archives and profile packaging pass container checks without changing attribute bytes", async () => {
  for (const name of ["static", "discrete", "continuous8", "continuous16"]) {
    const input = await source(`${name}.tsog`);
    assert.equal((await validateTSOGPackage(input)).legacy, true);
    await assert.rejects(validateTSOGPackage(input, { requireProfile: true }), /explicit/);
    const packaged = await packageTSOG(input);
    const report = await validateTSOGPackage(packaged, { requireProfile: true });
    assert.equal(report.level, "container");
    assert.deepEqual(report.profile, TSOG_PROFILE);
    assert.equal(report.audioCodecChecked, false);
    const before = await openZip(input), after = await openZip(packaged);
    for (const file of before.names.filter((file) => file !== "meta.json"))
      assert.deepEqual(await after.read(file), await before.read(file));
  }
  const audio = await packageTSOG(await source("static.tsog"), { audio: toneFile(), playback: { duration: 2 } });
  assert.equal((await validateTSOGPackage(audio)).audio, true);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(validateTSOGPackage(audio, { signal: controller.signal }), { name: "AbortError" });
});

test("lossless attribute header checks reject lossy, animated, truncated and inconsistent images", async () => {
  const archive = await openZip(await source());
  const original = await archive.read("means_l.webp");
  assert.deepEqual(inspectAttributeImage(original), { width: 16, height: 16 });
  const marker = Buffer.from(original).indexOf("VP8L");
  assert.ok(marker >= 12);
  for (const mutate of [
    (b) => b[0] = 0,
    (b) => b[4] ^= 1,
    (b) => b.set(Buffer.from("VP8 "), marker),
    (b) => b.set(Buffer.from("ANMF"), marker),
    (b) => b[marker + 8] = 0,
    (b) => b[marker + 12] |= 0xe0,
  ]) {
    const bytes = original.slice(); mutate(bytes);
    assert.throws(() => inspectAttributeImage(bytes), /lossless WebP/);
  }
  assert.throws(() => inspectAttributeImage(original.subarray(0, original.length - 1)), /lossless WebP/);
});

test("validation CLI reports its scope and exits nonzero on unsupported or corrupt packages", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tsog-conformance-"));
  const exec = promisify(execFile);
  const tool = new URL("../tools/validate-tsog.mjs", import.meta.url).pathname;
  try {
    const output = join(directory, "synthetic.tsog");
    const blob = await packageTSOG(await source());
    const bytes = Buffer.from(await blob.arrayBuffer());
    await writeFile(output, bytes);
    const result = await exec(process.execPath, [tool, output, "--require-profile", "--json"]);
    assert.equal(JSON.parse(result.stdout).level, "container");
    await assert.rejects(exec(process.execPath, [tool, fixture("static.tsog").pathname, "--require-profile", "--json"]), (error) => {
      assert.equal(error.code, 1);
      assert.equal(JSON.parse(error.stdout).valid, false);
      return true;
    });
    // A byte in an actual lossless stream changes without updating the ZIP CRC.
    const marker = bytes.indexOf("VP8L"); assert.ok(marker > 0);
    bytes[marker + 14] ^= 1;
    await writeFile(output, bytes);
    await assert.rejects(exec(process.execPath, [tool, output]), /checksum/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
