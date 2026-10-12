import { readModel } from "./model.js";

self.onmessage = async ({ data }) => {
  if (data.type !== "decode") return;
  try {
    const model = await readModel(data.blob, {
      ...data.options,
      onProgress: (progress) => self.postMessage({ type: "progress", progress }),
    });
    const buffers = ["positionTime", "velocityDuration", "covarianceA", "covarianceB", "sh", "alpha"]
      .map((key) => model[key].buffer);
    self.postMessage({ type: "decoded", model }, buffers);
  } catch (error) {
    self.postMessage({
      type: "error", name: error.name, message: error.message,
      unsupported: error.code === "TSOG_WEBGL_UNAVAILABLE",
    });
  }
};

self.postMessage({
  type: "ready",
  tsog: typeof OffscreenCanvas === "function" && typeof createImageBitmap === "function",
});
