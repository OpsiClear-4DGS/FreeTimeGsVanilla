import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolvePlayback, validatePlaybackMetadata } from "../playback.js";
import { packageTSOG } from "../tsog-package.js";
import { openZip } from "../zip.js";
import { toneFile } from "./audio-fixture.mjs";

const source = async (name = "continuous16") =>
  new Blob([
    await readFile(new URL(`./fixtures/tsog/${name}.tsog`, import.meta.url)),
  ]);
const metadata = async (archive) =>
  JSON.parse(new TextDecoder().decode(await archive.read("meta.json")));

test("file timing, speed and loop defaults respect explicit overrides and legacy files", () => {
  const continuous = {
    timelineMode: 0,
    nFrames: null,
    playback: { duration: 4, fps: 24, rate: 1.25, loop: false },
  };
  assert.deepEqual(resolvePlayback(continuous), {
    nFrames: 97,
    fps: 24,
    duration: 4,
    playbackRate: 1.25,
    loop: false,
    playable: true,
  });
  const override = resolvePlayback(continuous, {
    fps: 48,
    playbackRate: 2,
    loop: true,
  });
  assert.equal(override.duration, 2);
  assert.equal(override.playbackRate, 2);
  assert.equal(override.loop, true);
  assert.equal(resolvePlayback(continuous, { frames: 49 }).duration, 2);
  const discrete = {
    timelineMode: 1,
    nFrames: 4,
    fps: 24,
    playback: { duration: 2 },
  };
  assert.equal(resolvePlayback(discrete).fps, 2);
  assert.equal(resolvePlayback(discrete, { frames: 999, fps: 4 }).duration, 1);
  assert.equal(
    resolvePlayback({ timelineMode: 0, nFrames: 61 }, {}).duration,
    2,
  );
  assert.equal(resolvePlayback({ timelineMode: 0 }).duration, 299 / 30);
  assert.equal(
    resolvePlayback({ timelineMode: 2, nFrames: 1 }).playable,
    false,
  );
  assert.equal(
    resolvePlayback({ timelineMode: 2, nFrames: 1, playback: { duration: 4 } })
      .playable,
    true,
  );
});

test("invalid timing, audio references and conflicting discrete timing fail validation", () => {
  for (const playback of [
    null,
    [],
    { duration: 0 },
    { fps: Infinity },
    { rate: 0 },
    { rate: 8 },
    { loop: 1 },
  ])
    assert.throws(() => validatePlaybackMetadata({ playback }), /Invalid TSOG/);
  for (const audio of [
    null,
    [],
    {},
    { file: "../x.mp3" },
    { file: "https://example.test/x.mp3" },
    { file: "/x.mp3" },
    { file: "a\\x.mp3" },
    { file: "meta.json" },
    { file: "track.bin" },
    { file: "track.wav", volume: 2 },
    { file: "track.wav", mimeType: "text/html" },
  ])
    assert.throws(
      () => validatePlaybackMetadata({ timeline: { type: 1 }, audio }),
      /Invalid TSOG/,
    );
  assert.throws(
    () =>
      validatePlaybackMetadata({
        timeline: { type: 0, N: 4 },
        playback: { duration: 2, fps: 3 },
      }),
    /must equal/,
  );
  assert.throws(
    () => validatePlaybackMetadata({ audio: { file: "track.wav" } }),
    /static scene/,
  );
  assert.throws(
    () =>
      validatePlaybackMetadata({
        timeline: {},
        means: { files: ["track.wav"] },
        audio: { file: "track.wav" },
      }),
    /attribute images/,
  );
  validatePlaybackMetadata({
    timeline: {},
    playback: { rate: 0.25, loop: false },
    audio: { file: "audio/track.mp3" },
  });
});

test("packaging preserves every attribute byte, embeds audio, and replaces or removes old tracks", async () => {
  const input = await source(),
    original = await openZip(input),
    audio = toneFile();
  const packaged = await packageTSOG(input, {
    audio,
    playback: { duration: 4, fps: 24, rate: 1.25, loop: false },
    volume: 0.4,
  });
  const archive = await openZip(packaged),
    meta = await metadata(archive);
  assert.deepEqual(meta.playback, {
    duration: 4,
    fps: 24,
    rate: 1.25,
    loop: false,
  });
  assert.deepEqual(meta.audio, {
    file: "audio/track.wav",
    mimeType: "audio/wav",
    volume: 0.4,
  });
  assert.deepEqual(
    await archive.read(meta.audio.file),
    new Uint8Array(await audio.arrayBuffer()),
  );
  for (const name of original.names.filter((name) => name !== "meta.json"))
    assert.deepEqual(await archive.read(name), await original.read(name));
  const changed = await packageTSOG(packaged, {
    playback: { rate: 2 },
    volume: 0.8,
  });
  const changedMeta = await metadata(await openZip(changed));
  assert.equal(changedMeta.playback.duration, 4);
  assert.equal(changedMeta.playback.rate, 2);
  assert.equal(changedMeta.audio.volume, 0.8);
  const replacement = new File([await audio.arrayBuffer()], "replacement.wav");
  const replaced = await openZip(
    await packageTSOG(changed, { audio: replacement }),
  );
  assert.equal(
    replaced.names.filter((name) => name.startsWith("audio/")).length,
    1,
  );
  const removed = await openZip(await packageTSOG(changed, { audio: null }));
  assert.equal((await metadata(removed)).audio, undefined);
  assert.ok(!removed.names.includes(meta.audio.file));
  await assert.rejects(packageTSOG(input, { audio: new Blob() }), /nonempty/);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(packageTSOG(input, { signal: controller.signal }), {
    name: "AbortError",
  });
});

test("packaging CLI produces a readable .tsog and protects existing output", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tsog-audio-"));
  try {
    const input = join(directory, "source.tsog"),
      audio = join(directory, "tone.wav"),
      output = join(directory, "demo.tsog");
    await writeFile(
      input,
      new Uint8Array(await (await source()).arrayBuffer()),
    );
    await writeFile(audio, new Uint8Array(await toneFile().arrayBuffer()));
    const args = [
      new URL("../tools/package-tsog.mjs", import.meta.url).pathname,
      input,
      output,
      "--audio",
      audio,
      "--duration",
      "4",
      "--fps",
      "24",
      "--speed",
      "1.25",
      "--no-loop",
      "--volume",
      "0.4",
    ];
    await promisify(execFile)(process.execPath, args);
    const meta = await metadata(
      await openZip(new Blob([await readFile(output)])),
    );
    assert.equal(meta.playback.rate, 1.25);
    assert.equal(meta.playback.loop, false);
    assert.equal(meta.audio.mimeType, "audio/wav");
    await assert.rejects(promisify(execFile)(process.execPath, args), /EEXIST/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
