#!/usr/bin/env node
import { readFile, writeFile } from "node:fs/promises";
import { basename } from "node:path";
import { parseArgs } from "node:util";
import { packageTSOG } from "../tsog-package.js";

const usage = `Usage: node tools/package-tsog.mjs input.tsog output.tsog [options]
  --audio track.mp3    Add or replace the embedded audio file
  --remove-audio       Remove the current audio track
  --duration 10        Clip duration in seconds at normal speed
  --fps 30            Display frame rate
  --speed 1           Playback multiplier (0.25 to 4)
  --loop / --no-loop   Default looping behavior
  --volume 0.8        Default audio volume (0 to 1)
Existing output files are never overwritten. Unspecified metadata is retained.`;

try {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      audio: { type: "string" },
      "remove-audio": { type: "boolean" },
      duration: { type: "string" },
      fps: { type: "string" },
      speed: { type: "string" },
      loop: { type: "boolean" },
      "no-loop": { type: "boolean" },
      volume: { type: "string" },
      help: { type: "boolean" },
    },
  });
  if (values.help) console.log(usage);
  else {
    if (positionals.length !== 2) throw new Error(usage);
    const [input, output] = positionals;
    if (!output.toLowerCase().endsWith(".tsog"))
      throw new Error("Output must use the .tsog extension.");
    if (values.audio && values["remove-audio"])
      throw new Error("Choose --audio or --remove-audio.");
    if (values.loop && values["no-loop"])
      throw new Error("Choose --loop or --no-loop.");
    const playback = {};
    for (const [flag, key] of [
      ["duration", "duration"],
      ["fps", "fps"],
      ["speed", "rate"],
    ])
      if (values[flag] !== undefined) playback[key] = Number(values[flag]);
    if (values.loop || values["no-loop"]) playback.loop = Boolean(values.loop);
    const audio = values.audio
      ? new File([await readFile(values.audio)], basename(values.audio))
      : values["remove-audio"]
        ? null
        : undefined;
    const result = await packageTSOG(new Blob([await readFile(input)]), {
      playback,
      audio,
      volume: values.volume === undefined ? undefined : Number(values.volume),
    });
    await writeFile(output, new Uint8Array(await result.arrayBuffer()), {
      flag: "wx",
    });
    console.log(`Saved ${output} (${result.size.toLocaleString()} bytes).`);
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
