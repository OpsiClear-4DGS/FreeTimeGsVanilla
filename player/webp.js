// Attribute images carry integer codes, including in RGB when alpha is small.
// A 2D canvas would round those codes through premultiplied alpha.
// Inspect the container without decoding pixels (also usable from Node.js).
export function inspectAttributeImage(bytes) {
  const fail = () => { throw new Error("Invalid TSOG: expected a still lossless WebP attribute image."); };
  const tag = (offset) => String.fromCharCode(...bytes.subarray(offset, offset + 4));
  if (bytes.length < 25 || tag(0) !== "RIFF" || tag(8) !== "WEBP") fail();
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(4, true) + 8 !== bytes.length) fail();
  let dimensions, canvas;
  for (let offset = 12; offset < bytes.length;) {
    if (offset + 8 > bytes.length) fail();
    const type = tag(offset), size = view.getUint32(offset + 4, true);
    const start = offset + 8, end = start + size;
    if (end + (size & 1) > bytes.length) fail();
    if (["VP8 ", "ANIM", "ANMF", "ALPH"].includes(type)) fail();
    if (type === "VP8L") {
      if (dimensions || size < 5 || bytes[start] !== 0x2f) fail();
      const bits = view.getUint32(start + 1, true);
      if (bits >>> 29) fail();
      dimensions = { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
    } else if (type === "VP8X") {
      if (canvas || offset !== 12 || size !== 10 || (bytes[start] & 0xc3) ||
          bytes[start + 1] || bytes[start + 2] || bytes[start + 3]) fail();
      const uint24 = (i) => bytes[i] + 256 * bytes[i + 1] + 65536 * bytes[i + 2];
      canvas = { width: uint24(start + 4) + 1, height: uint24(start + 7) + 1 };
    }
    offset = end + (size & 1);
  }
  if (!dimensions || (canvas && (canvas.width !== dimensions.width || canvas.height !== dimensions.height))) fail();
  return dimensions;
}

export class AttributeImages {
  #gl;
  #texture;
  #framebuffer;

  async decode(bytes, signal) {
    signal?.throwIfAborted();
    inspectAttributeImage(bytes);
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/webp" }), {
      premultiplyAlpha: "none", colorSpaceConversion: "none", imageOrientation: "none",
    });
    try {
      signal?.throwIfAborted();
      if (!this.#gl) {
        const canvas = typeof OffscreenCanvas === "function"
          ? new OffscreenCanvas(1, 1) : document.createElement("canvas");
        this.#gl = canvas.getContext("webgl2", { antialias: false, depth: false });
        if (!this.#gl) throw new Error("WebGL2 is required to decode TSOG attribute images.");
        this.#texture = this.#gl.createTexture();
        this.#framebuffer = this.#gl.createFramebuffer();
      }
      const gl = this.#gl;
      const { width, height } = bitmap;
      const max = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      if (width > max || height > max) throw new Error("A TSOG attribute image exceeds this GPU's texture capacity.");
      gl.bindTexture(gl.TEXTURE_2D, this.#texture);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.#framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.#texture, 0);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
        throw new Error("Could not read a TSOG attribute image on this GPU.");
      const rgba = new Uint8Array(width * height * 4);
      // With no upload flip, texture row zero is the source image's top row.
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("Could not decode a TSOG attribute image.");
      return { width, height, rgba };
    } finally {
      bitmap.close();
    }
  }

  destroy() {
    const gl = this.#gl;
    if (!gl) return;
    gl.deleteTexture(this.#texture);
    gl.deleteFramebuffer(this.#framebuffer);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    this.#gl = null;
  }
}
