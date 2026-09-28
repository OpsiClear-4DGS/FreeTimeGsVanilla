import { readFTGS } from "./ftgs.js?v=5";

/** Detect packaged TSOG by its ZIP signature; keep filename-free Blob loading. */
export async function readModel(blob, options = {}) {
  options.signal?.throwIfAborted();
  const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  if (signature[0] === 0x50 && signature[1] === 0x4b && signature[2] === 3 && signature[3] === 4) {
    const { readTSOG } = await import("./tsog.js?v=5");
    return readTSOG(blob, options);
  }
  return { ...await readFTGS(blob, options), format: "ftgs-ply", timelineMode: 0 };
}
