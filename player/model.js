import { readFTGS } from "./ftgs.js?v=6";

/** Detect packaged TSOG by its ZIP signature; keep filename-free Blob loading. */
export async function readModel(blob, options = {}) {
  options.signal?.throwIfAborted();
  const signature = new DataView(await blob.slice(0, 4).arrayBuffer());
  if (
    signature.byteLength === 4 &&
    signature.getUint32(0, true) === 0x04034b50
  ) {
    const { readTSOG } = await import("./tsog.js?v=8");
    return readTSOG(blob, options);
  }
  return {
    ...(await readFTGS(blob, options)),
    format: "ftgs-ply",
    timelineMode: 0,
  };
}
