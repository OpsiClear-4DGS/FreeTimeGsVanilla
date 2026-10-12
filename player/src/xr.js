// Native WebXR presentation and model-to-room transforms. No UI or dependencies.
export function canUseXR() {
  if (!globalThis.isSecureContext || !globalThis.navigator?.xr) return false;
  const policy = document.permissionsPolicy ?? document.featurePolicy;
  return !policy || policy.allowsFeature("xr-spatial-tracking");
}

export async function isARSupported() {
  if (!canUseXR()) return false;
  try {
    return await navigator.xr.isSessionSupported("immersive-ar");
  } catch {
    return false; // Includes an embedding page that disallows spatial tracking.
  }
}

export function multiply4(a, b) {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++)
    for (let row = 0; row < 4; row++)
      for (let k = 0; k < 4; k++)
        out[4 * col + row] += a[4 * k + row] * b[4 * col + k];
  return out;
}

// Inverse for the rigid, uniformly scaled transforms used for scene placement.
export function inversePlacement(m) {
  const out = new Float32Array(16);
  const scaleSquared = m[0] ** 2 + m[1] ** 2 + m[2] ** 2;
  for (let col = 0; col < 3; col++)
    for (let row = 0; row < 3; row++)
      out[4 * col + row] = m[4 * row + col] / scaleSquared;
  for (let row = 0; row < 3; row++)
    out[12 + row] = -(out[row] * m[12] + out[4 + row] * m[13] + out[8 + row] * m[14]);
  out[15] = 1;
  return out;
}

export function placementMatrix(bounds, up, size, pose, onSurface = false) {
  const s = size / (2 * bounds.radius);
  const local = up === "z"
    ? new Float32Array([s,0,0,0, 0,0,-s,0, 0,s,0,0, 0,0,0,1])
    : new Float32Array([s,0,0,0, 0,s,0,0, 0,0,s,0, 0,0,0,1]);
  const anchor = [...bounds.center];
  const vertical = up === "z" ? 2 : 1;
  if (onSurface) anchor[vertical] = bounds.min[vertical];
  for (let row = 0; row < 3; row++)
    local[12 + row] = -(local[row] * anchor[0] + local[4 + row] * anchor[1] + local[8 + row] * anchor[2]);
  return multiply4(pose, local);
}

export function previewPose(viewer) {
  const length = Math.hypot(viewer[8], viewer[10]);
  const x = length > 1e-5 ? viewer[8] / length : 0;
  const z = length > 1e-5 ? viewer[10] / length : 1;
  return new Float32Array([
    z,0,-x,0, 0,1,0,0, x,0,z,0,
    viewer[12] - 1.5 * viewer[8],
    viewer[13] - 1.5 * viewer[9],
    viewer[14] - 1.5 * viewer[10], 1,
  ]);
}

export function xrCamera(transform, projection, model) {
  const eyeInModel = multiply4(inversePlacement(model), transform.matrix);
  return {
    view: multiply4(transform.inverse.matrix, model),
    eye: eyeInModel.slice(12, 15),
    projection,
    near: 0.01,
  };
}

export class ARPresentation {
  active = false;
  ended = false;
  placed = false;
  surface = false;
  session = null;
  hitSource = null;
  model = null;
  #events = new AbortController();
  #raf;

  constructor(renderer, { bounds, up, size, resolution, onFrame, onEnd, onChange, onError }) {
    Object.assign(this, { renderer, bounds, up, size, resolution, onFrame, onEnd, onChange, onError });
  }

  async start(overlayRoot) {
    this.#assertOpen();
    if (!canUseXR())
      throw new Error("AR needs HTTPS (or localhost), a WebXR AR capable device, and spatial-tracking permission.");
    const init = { requiredFeatures: ["local"], optionalFeatures: ["hit-test"] };
    if (overlayRoot) {
      init.optionalFeatures.push("dom-overlay");
      init.domOverlay = { root: overlayRoot };
    }
    // Request synchronously from the user's gesture, before any other await.
    const session = await navigator.xr.requestSession("immersive-ar", init);
    this.session = session;
    if (this.ended) {
      await session.end().catch(() => {});
      throw new DOMException("AR was cancelled.", "AbortError");
    }
    const options = { signal: this.#events.signal };
    session.addEventListener("end", () => this.#finish(), options);
    session.addEventListener("select", () => {
      if (!this.model || this.placed) return;
      this.placed = true;
      this.onChange();
    }, options);
    try {
      const gl = this.renderer.gl;
      await gl.makeXRCompatible();
      this.#assertOpen();
      this.layer = new XRWebGLLayer(session, gl, {
        alpha: true, depth: false, stencil: false, antialias: false,
        framebufferScaleFactor: this.resolution,
      });
      session.updateRenderState({ baseLayer: this.layer, depthNear: 0.01, depthFar: 100 });
      this.space = await session.requestReferenceSpace("local");
      this.#assertOpen();
      this.space.addEventListener("reset", () => this.reset(), options);
      try {
        const viewer = await session.requestReferenceSpace("viewer");
        this.#assertOpen();
        const source = await session.requestHitTestSource({ space: viewer });
        if (this.ended) source.cancel();
        else this.hitSource = source;
      } catch {
        // Surface detection is optional; placement in front works without it.
      }
      this.#assertOpen();
      this.active = true;
      this.#raf = session.requestAnimationFrame(this.#frame);
    } catch (error) {
      await this.stop();
      throw error;
    }
  }

  #assertOpen() {
    if (this.ended) throw new DOMException("AR was cancelled.", "AbortError");
  }

  reset() {
    this.placed = false;
    this.surface = false;
    this.model = null;
    this.onChange();
  }

  #frame = (now, frame) => {
    if (this.ended) return;
    this.#raf = this.session.requestAnimationFrame(this.#frame);
    try {
      this.renderer.beginXRFrame(this.layer);
      const pose = this.session.visibilityState === "hidden" ? null : frame.getViewerPose(this.space);
      if (!pose) {
        this.onFrame(now, null);
        return;
      }
      if (!this.placed) {
        const hit = this.hitSource
          ? frame.getHitTestResults(this.hitSource)[0]?.getPose(this.space)
          : null;
        const surfaceChanged = this.surface !== Boolean(hit);
        this.surface = Boolean(hit);
        this.model = placementMatrix(
          this.bounds, this.up, this.size,
          hit?.transform.matrix ?? previewPose(pose.transform.matrix), this.surface,
        );
        if (surfaceChanged) this.onChange();
      }
      if (this.ended || !this.model) return; // A state listener may exit or reposition.
      const views = pose.views.map((view) => ({
        camera: xrCamera(view.transform, view.projectionMatrix, this.model),
        viewport: this.layer.getViewport(view),
      }));
      this.onFrame(now, {
        views,
        camera: xrCamera(pose.transform, null, this.model),
      });
    } catch (error) {
      this.onError(error);
      void this.stop();
    }
  };

  #finish() {
    if (this.ended) return;
    this.ended = true;
    this.active = false;
    try { this.session?.cancelAnimationFrame(this.#raf); } catch { /* Already ended. */ }
    this.#events.abort();
    try { this.hitSource?.cancel(); } catch { /* The device may have cancelled it. */ }
    this.hitSource = null;
    this.model = null;
    const gl = this.renderer.gl;
    if (!gl.isContextLost()) gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.onEnd();
  }

  async stop() {
    if (this.ended) return;
    this.#finish();
    try {
      await this.session?.end();
    } catch {
      // Ending a session which the device has already ended is harmless.
    }
  }
}
