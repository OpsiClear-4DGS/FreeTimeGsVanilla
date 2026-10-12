// Download large website examples in bounded requests; the canvas API stays independent.
const chunkSize = 4 * 1024 * 1024;

async function request(url, signal, headers, consume) {
  let failure;
  for (let attempt = 0; attempt < 3; attempt++) {
    signal?.throwIfAborted();
    try {
      const response = await fetch(url, {
        signal, headers, ...(attempt ? { cache: "reload" } : {}),
      });
      if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.permanent = response.status < 500 && ![408, 429].includes(response.status);
        throw error;
      }
      return await consume(response);
    } catch (error) {
      signal?.throwIfAborted();
      failure = error;
      if (error.permanent || attempt === 2) break;
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
    }
  }
  throw new Error(`Could not fetch ${url}: ${failure.message}`, { cause: failure });
}

export function downloadJSON(url, { signal } = {}) {
  return request(url, signal, undefined, (response) => response.json());
}

/** Reassemble byte ranges exactly, retry interrupted requests, and stop on cancellation. */
export async function downloadFile(url, { bytes, signal, onProgress = () => {} } = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  signal?.throwIfAborted();
  signal?.addEventListener("abort", cancel, { once: true });
  const active = controller.signal;
  try {
    onProgress(0);
    if (!Number.isSafeInteger(bytes) || bytes <= chunkSize) {
      const blob = await request(url, active, undefined, (response) => response.blob());
      onProgress(1);
      return blob;
    }
    let completed = 0;
    const parts = new Array(Math.ceil(bytes / chunkSize));
    async function part(index) {
      const start = index * chunkSize;
      const end = Math.min(bytes, start + chunkSize) - 1;
      return request(url, active, { Range: `bytes=${start}-${end}` }, async (response) => {
        // A server may ignore Range. Accept a complete first response without redownloading it.
        if (index === 0 && response.status === 200) {
          const blob = await response.blob();
          if (blob.size !== bytes) throw new Error("Incomplete example download.");
          return blob;
        }
        if (response.status !== 206 || response.headers.get("Content-Range") !== `bytes ${start}-${end}/${bytes}`)
          throw new Error("The server returned an incorrect byte range.");
        const blob = await response.blob();
        if (blob.size !== end - start + 1) throw new Error("Incomplete example download.");
        return blob;
      });
    }
    parts[0] = await part(0);
    if (parts[0].size === bytes) {
      onProgress(1);
      return parts[0];
    }
    completed += parts[0].size;
    onProgress(completed / bytes);
    let next = 1;
    async function worker() {
      while (next < parts.length) {
        const index = next++;
        parts[index] = await part(index);
        completed += parts[index].size;
        onProgress(completed / bytes);
      }
    }
    await Promise.all(Array.from({ length: Math.min(3, parts.length - 1) }, worker));
    active.throwIfAborted();
    return new Blob(parts, { type: "application/octet-stream" });
  } catch (error) {
    controller.abort(); // Stop other range requests when one fails.
    throw error;
  } finally {
    signal?.removeEventListener("abort", cancel);
  }
}
