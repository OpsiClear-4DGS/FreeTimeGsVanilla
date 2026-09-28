// Deterministic WebXR test device. The app's parser, worker and WebGL are real.
// This deliberately does not claim to replace testing camera passthrough hardware.
(() => {
  const identity = () => new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
  const transform = (x=0, y=0, z=0) => {
    const matrix = identity(), inverse = identity();
    matrix[12]=x; matrix[13]=y; matrix[14]=z;
    inverse[12]=-x; inverse[13]=-y; inverse[14]=-z;
    return { matrix, inverse: { matrix: inverse } };
  };
  const projection = (offset=0) => new Float32Array([
    1,0,0,0, 0,1,0,0, offset,0,-1.0002,-1, 0,0,-0.020002,0,
  ]);
  class MockSession extends EventTarget {
    callbacks = new Map();
    next = 1;
    ended = false;
    visibilityState = "visible";
    renderState = {};
    space = new EventTarget();
    endCount = 0;
    cancelledSources = 0;
    async requestReferenceSpace(type) {
      if (xr.failSpace && type === "local") throw new Error("Reference space unavailable");
      return type === "local" ? this.space : new EventTarget();
    }
    async requestHitTestSource() {
      if (!xr.hitTest) throw new DOMException("No surface detection", "NotSupportedError");
      return { cancel: () => this.cancelledSources++ };
    }
    updateRenderState(state) { Object.assign(this.renderState, state); }
    requestAnimationFrame(callback) {
      const id = this.next++;
      this.callbacks.set(id, callback);
      return id;
    }
    cancelAnimationFrame(id) { this.callbacks.delete(id); }
    async end() {
      if (this.ended) return;
      this.endCount++;
      this.ended = true;
      this.callbacks.clear();
      this.dispatchEvent(new Event("end"));
      const layer = this.renderState.baseLayer;
      if (layer) {
        layer.gl.deleteFramebuffer(layer.framebuffer);
        layer.gl.deleteTexture(layer.texture);
      }
    }
    step({ x=0, y=0, z=0, stereo=true, tracked=true, surface=null, advance=17 } = {}) {
      if (this.ended) throw new Error("Stepped an ended session");
      xr.now = Math.max(performance.now(), (xr.now ?? 0) + advance);
      const views = (stereo ? [-0.032, 0.032] : [0]).map((eye, i) => ({
        index: i, stereo,
        transform: transform(x+eye, y, z),
        projectionMatrix: projection(stereo ? (i ? -0.08 : 0.08) : 0),
      }));
      const frame = {
        getViewerPose: () => tracked ? { transform: transform(x,y,z), views } : null,
        getHitTestResults: () => surface ? [{ getPose: () => ({ transform: transform(...surface) }) }] : [],
      };
      xr.inFrame = true;
      const callbacks = [...this.callbacks.values()];
      this.callbacks.clear();
      for (const callback of callbacks) callback(xr.now, frame);
      xr.inFrame = false;
    }
  }
  const xr = Object.assign(new EventTarget(), {
    supported: true, hitTest: true, reject: false, failSpace: false,
    defer: false, sessions: [], draws: [], violations: [], inFrame: false,
    async isSessionSupported(mode) { return mode === "immersive-ar" && this.supported; },
    async requestSession(mode, options) {
      this.lastRequest = { mode, options, userGesture: navigator.userActivation.isActive };
      if (this.reject) throw new DOMException("AR permission denied", "NotAllowedError");
      if (this.defer) await new Promise(resolve => { this.resolveRequest = resolve; });
      const session = new MockSession();
      session.domOverlayState = options.domOverlay ? { type: "screen" } : null;
      this.sessions.push(session);
      this.session = session;
      return session;
    },
    pixels() {
      const layer = this.session.renderState.baseLayer, gl = layer.gl;
      gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
      const data = new Uint8Array(640*320*4);
      gl.readPixels(0,0,640,320,gl.RGBA,gl.UNSIGNED_BYTE,data);
      const count = [0,0];
      for (let y=0; y<320; y++) for (let x=0; x<640; x++)
        if (data[4*(y*640+x)+3]) count[x<320 ? 0 : 1]++;
      return { count, corner: [...data.slice(0,4)], error: gl.getError() };
    },
  });
  Object.defineProperty(navigator, "xr", { configurable: true, value: xr });
  WebGL2RenderingContext.prototype.makeXRCompatible = async function() {};
  window.XRWebGLLayer = class {
    constructor(session, gl, options) {
      this.gl = gl;
      this.options = options;
      this.framebuffer = gl.createFramebuffer();
      this.texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 640, 320, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.texture, 0);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    }
    getViewport(view) { return { x: view.index*320, y: 0, width: view.stereo ? 320 : 640, height: 320 }; }
  };
  const draw = WebGL2RenderingContext.prototype.drawArraysInstanced;
  WebGL2RenderingContext.prototype.drawArraysInstanced = function(...args) {
    const layer = xr.session?.renderState.baseLayer;
    if (layer && this.getParameter(this.FRAMEBUFFER_BINDING) === layer.framebuffer) {
      if (!xr.inFrame) xr.violations.push("Drew to XR outside its animation frame");
      const program = this.getParameter(this.CURRENT_PROGRAM);
      xr.draws.push({
        viewport: [...this.getParameter(this.VIEWPORT)],
        view: [...this.getUniform(program, this.getUniformLocation(program,"view"))],
        projection: [...this.getUniform(program, this.getUniformLocation(program,"projection"))],
        time: this.getUniform(program, this.getUniformLocation(program,"time")),
      });
    }
    return draw.apply(this, args);
  };
})();
