// Attribute images carry integer codes, including in RGB when alpha is small.
// A 2D canvas would round those codes through premultiplied alpha.
export class AttributeImages {
  #gl;
  #texture;
  #framebuffer;

  async decode(bytes, signal) {
    signal?.throwIfAborted();
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
