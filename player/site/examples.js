import { downloadJSON, downloadFile } from "./download.js?v=2";

const presets = Object.freeze({
  "jensen-2m": new URL("../examples/assets/jensen-2m.json", import.meta.url),
});

/** Example assets are fetched only after an explicit example selection. */
export async function readExample(id, { signal } = {}) {
  const metadataURL = presets[id];
  if (!metadataURL) throw new Error(`Unknown example: ${id}`);
  const metadata = await downloadJSON(metadataURL, { signal });
  return { ...metadata, source: new URL(metadata.file, metadataURL) };
}

export async function downloadExample(example, options) {
  // Opaque sandbox origins and remote hosts may hide Content-Range from CORS.
  // Keep validated ranges for local assets; retry a whole request otherwise.
  const local = example.source.origin === globalThis.origin;
  const blob = await downloadFile(example.source, {
    ...options, bytes: local ? example.bytes : undefined,
  });
  if (blob.size !== example.bytes) throw new Error("Incomplete example download.");
  return blob;
}
