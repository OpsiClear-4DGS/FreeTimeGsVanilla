#!/usr/bin/env node
// A static deployment: preserve module/worker URLs; no bundle or runtime dependencies.
import { cp, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const output = new URL("dist/", root);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const path of [
  "index.html", "index.js", "player.js", "embed.js", ".nojekyll",
  "src", "site", "examples", "docs", "tsog.schema.json",
  "LICENSE", "README.md",
]) {
  await cp(new URL(path, root), new URL(path, output), { recursive: true });
}
console.log(`Static player, reusable modules and Jensen example: ${fileURLToPath(output)}`);
