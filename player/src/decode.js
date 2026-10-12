import { readModel } from "./model.js";
import { createModuleWorker } from "./worker.js";

/** Decode away from the UI. Older browsers use the same decoder on the main thread. */
export async function readModelInWorker(blob, options = {}) {
  const { signal, onProgress = () => {}, ...decodeOptions } = options;
  signal?.throwIfAborted();
  const signature = new DataView(await blob.slice(0, 4).arrayBuffer());
  signal?.throwIfAborted();
  const tsog = signature.byteLength === 4 && signature.getUint32(0, true) === 0x04034b50;
  let worker;
  try {
    worker = createModuleWorker(new URL("./decode-worker.js", import.meta.url));
  } catch {
    return readModel(blob, options);
  }
  const model = await new Promise((resolve, reject) => {
    let finished = false, started = false;
    const finish = (settle, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(startup);
      signal?.removeEventListener("abort", abort);
      worker.onmessage = worker.onerror = worker.onmessageerror = null;
      worker.terminate();
      settle(value);
    };
    const abort = () => finish(reject, signal.reason);
    // A blocked/unresponsive bootstrap must not leave loading stuck forever.
    const startup = setTimeout(() => finish(resolve, null), 5000);
    signal?.addEventListener("abort", abort, { once: true });
    worker.onmessage = ({ data }) => {
      if (finished) return;
      try {
        if (data.type === "ready" && !started) {
          clearTimeout(startup);
          if (tsog && !data.tsog) return finish(resolve, null);
          started = true;
          worker.postMessage({ type: "decode", blob, options: decodeOptions });
        } else if (data.type === "progress") onProgress(data.progress);
        else if (data.type === "decoded") finish(resolve, data.model);
        else if (data.type === "error") {
          if (data.unsupported) return finish(resolve, null);
          const error = new Error(data.message);
          error.name = data.name ?? "Error";
          finish(reject, error);
        }
      } catch (error) { finish(reject, error); }
    };
    worker.onerror = (event) => {
      event.preventDefault();
      if (!started) finish(resolve, null);
      else finish(reject, new Error(`Decode worker failed: ${event.message}`));
    };
    worker.onmessageerror = () => finish(reject, new Error("Could not receive the decoded model."));
    if (signal?.aborted) abort();
  });
  signal?.throwIfAborted();
  return model ?? readModel(blob, options);
}
