#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { validateTSOGPackage } from "../tsog-validate.js";

const usage = `Usage: node tools/validate-tsog.mjs scene.tsog [--require-profile] [--json]
Checks metadata, timing, referenced entries, ZIP checksums and lossless WebP headers.
Image pixels and audio codecs are not decoded. See TSOG-SPEC.md for full checks.
--require-profile  Require an explicit TSOG Playback Profile 1 declaration
--json             Print a machine-readable result
Exit status: 0 passes these checks; 1 invalid input or command error.`;

let json = false;
try {
  const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    "require-profile": { type: "boolean" }, json: { type: "boolean" }, help: { type: "boolean" },
  } });
  json = Boolean(values.json);
  if (values.help) console.log(usage);
  else {
    if (positionals.length !== 1) throw new Error(usage);
    const result = await validateTSOGPackage(new Blob([await readFile(positionals[0])]), {
      requireProfile: Boolean(values["require-profile"]),
    });
    console.log(json ? JSON.stringify(result, null, 2) :
      `PASS metadata/container: ${result.count} Gaussians, ${result.timeline}, ${result.images} images, audio ${result.audio ? "present" : "absent"}.\n` +
      `${result.legacy ? "Legacy v4 (no profile declaration)." : "TSOG Playback Profile 1."} Image pixels and audio codecs were not decoded.`);
  }
} catch (error) {
  if (json) console.log(JSON.stringify({ valid: false, error: error.message }));
  else console.error(error.message);
  process.exitCode = 1;
}
