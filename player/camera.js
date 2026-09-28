const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const unit = (v) => {
  const n = Math.hypot(...v);
  return v.map((x) => x / n);
};
const movementKeys = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE"]);

export function lookAt(eye, target, up) {
  const back = unit(eye.map((v, i) => v - target[i]));
  const right = unit(cross(up, back)),
    vertical = cross(back, right);
  return new Float32Array([
    right[0],
    vertical[0],
    back[0],
    0,
    right[1],
    vertical[1],
    back[1],
    0,
    right[2],
    vertical[2],
    back[2],
    0,
    -dot(right, eye),
    -dot(vertical, eye),
    -dot(back, eye),
    1,
  ]);
}

export function validateCameraView({ eye, target, up = "y", fov = 45 }) {
  if (
    ![eye, target].every(
      (v) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite),
    ) ||
    !["y", "z"].includes(up) ||
    !Number.isFinite(fov) ||
    fov < 10 ||
    fov > 120
  )
    throw new Error(
      "Invalid camera view: use finite eye/target vectors, y/z up and a 10–120 degree fov.",
    );
  const distance = Math.hypot(...eye.map((v, i) => v - target[i]));
  if (!(distance > 1e-6 && Number.isFinite(distance)))
    throw new Error("Camera eye and target must be distinct finite positions.");
  return { eye: [...eye], target: [...target], up, fov };
}

export class OrbitCamera {
  constructor(canvas, onChange) {
    this.canvas = canvas;
    this.onChange = onChange;
    this.keys = new Set();
    this.fast = false;
    this.enabled = true;
    this.addedTabIndex = !canvas.hasAttribute("tabindex");
    if (this.addedTabIndex) canvas.tabIndex = 0;
    this.upAxis = "y";
    this.bounds = { center: [0, 0, 0], radius: 1 };
    this.fit();
    this.events = new AbortController();
    const options = { signal: this.events.signal };
    canvas.addEventListener(
      "keydown",
      (event) => {
        if (!this.enabled) return;
        if (
          event.ctrlKey ||
          event.metaKey ||
          event.altKey ||
          event.isComposing
        ) {
          this.stopMoving();
          return;
        }
        if (event.defaultPrevented) return;
        this.fast = event.shiftKey;
        if (!movementKeys.has(event.code)) return;
        event.preventDefault();
        // After a blur/reset, require a fresh press rather than an old key repeat.
        if (!event.repeat || this.keys.has(event.code))
          this.keys.add(event.code);
      },
      options,
    );
    canvas.addEventListener(
      "keyup",
      (event) => {
        this.keys.delete(event.code);
        this.fast = event.shiftKey;
      },
      options,
    );
    canvas.addEventListener("blur", () => this.stopMoving(), options);
    canvas.ownerDocument.defaultView.addEventListener(
      "blur",
      () => this.stopMoving(),
      options,
    );
    canvas.ownerDocument.addEventListener(
      "visibilitychange",
      () => {
        if (canvas.ownerDocument.hidden) this.stopMoving();
      },
      options,
    );
    const pointers = new Map();
    canvas.addEventListener("contextmenu", (e) => e.preventDefault(), options);
    canvas.addEventListener(
      "pointerdown",
      (e) => {
        if (!this.enabled) return;
        canvas.focus();
        canvas.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      },
      options,
    );
    canvas.addEventListener(
      "pointermove",
      (e) => {
        if (!this.enabled) return;
        const old = pointers.get(e.pointerId);
        if (!old) return;
        const dx = e.clientX - old.x,
          dy = e.clientY - old.y;
        if (pointers.size === 2) {
          const other = [...pointers.entries()].find(
            ([id]) => id !== e.pointerId,
          )[1];
          const before = Math.hypot(old.x - other.x, old.y - other.y);
          const after = Math.hypot(e.clientX - other.x, e.clientY - other.y);
          if (after > 0) this.zoom(before / after);
          this.pan(dx / 2, dy / 2);
        } else if (e.buttons === 2 || e.buttons === 4 || e.shiftKey)
          this.pan(dx, dy);
        else {
          this.yaw -= dx * 0.006;
          this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch + dy * 0.006));
        }
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        this.onChange();
      },
      options,
    );
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
      canvas.addEventListener(
        name,
        (e) => pointers.delete(e.pointerId),
        options,
      );
    canvas.addEventListener(
      "wheel",
      (e) => {
        if (!this.enabled) return;
        e.preventDefault();
        this.zoom(Math.exp(e.deltaY * 0.001));
        this.onChange();
      },
      { ...options, passive: false },
    );
  }
  fit(bounds = this.bounds) {
    this.stopMoving();
    this.bounds = bounds;
    this.target = [...bounds.center];
    const aspect = Math.max(
      0.1,
      this.canvas.clientWidth / Math.max(1, this.canvas.clientHeight),
    );
    const halfAngle = Math.atan(Math.tan(Math.PI / 8) * Math.min(1, aspect));
    this.distance = (bounds.radius / Math.sin(halfAngle)) * 1.1;
    this.yaw = 0.45;
    this.pitch = 0.12;
    this.fov = Math.PI / 4;
    this.onChange?.();
  }
  restore(view) {
    const { eye, target, up, fov } = validateCameraView(view);
    this.stopMoving();
    const offset = eye.map((v, i) => v - target[i]);
    const distance = Math.hypot(...offset);
    this.target = [...target];
    this.distance = distance;
    this.upAxis = up;
    this.fov = (fov * Math.PI) / 180;
    this.yaw = Math.atan2(offset[0], up === "z" ? -offset[1] : offset[2]);
    this.pitch = Math.asin(
      Math.max(
        -0.999999,
        Math.min(0.999999, offset[up === "z" ? 2 : 1] / distance),
      ),
    );
    this.onChange?.();
  }
  zoom(factor) {
    this.distance = Math.max(
      this.bounds.radius * 0.02,
      Math.min(this.bounds.radius * 100, this.distance * factor),
    );
  }
  pan(dx, dy) {
    const { view } = this.snapshot(),
      scale = this.distance / Math.max(1, this.canvas.clientHeight);
    for (let i = 0; i < 3; i++)
      this.target[i] += (-dx * view[4 * i] + dy * view[4 * i + 1]) * scale;
  }
  stopMoving() {
    this.keys?.clear();
    this.fast = false;
  }
  update(seconds) {
    if (!this.keys.size) return;
    if (!this.canvas.matches(":focus") || this.canvas.ownerDocument.hidden) {
      this.stopMoving();
      return;
    }
    const held = (code) => Number(this.keys.has(code));
    this.move(
      held("KeyW") - held("KeyS"),
      held("KeyD") - held("KeyA"),
      held("KeyE") - held("KeyQ"),
      seconds,
      this.fast,
    );
  }
  move(forward, right, up, seconds, fast = false) {
    if (!(seconds > 0) || !Number.isFinite(seconds)) return;
    const { view } = this.snapshot();
    const direction = [0, 1, 2].map(
      (axis) =>
        right * view[4 * axis] -
        forward * view[4 * axis + 2] +
        (axis === (this.upAxis === "z" ? 2 : 1) ? up : 0),
    );
    const length = Math.hypot(...direction);
    if (length < 1e-8) return;
    const speed = Math.max(
      this.bounds.radius * 0.01,
      Math.min(this.bounds.radius, this.distance),
    );
    const step = (speed * (fast ? 4 : 1) * Math.min(seconds, 0.1)) / length;
    // Translate the eye and its orbit target together, preserving the view angle.
    for (let axis = 0; axis < 3; axis++)
      this.target[axis] += direction[axis] * step;
    this.onChange?.();
  }
  snapshot() {
    const horizontal = Math.cos(this.pitch) * this.distance;
    const offset = [
      Math.sin(this.yaw) * horizontal,
      Math.sin(this.pitch) * this.distance,
      Math.cos(this.yaw) * horizontal,
    ];
    if (this.upAxis === "z") [offset[1], offset[2]] = [-offset[2], offset[1]];
    const eye = this.target.map((v, i) => v + offset[i]);
    return {
      eye,
      view: lookAt(
        eye,
        this.target,
        this.upAxis === "z" ? [0, 0, 1] : [0, 1, 0],
      ),
      near: Math.max(1e-6, this.bounds.radius * 0.0001),
      fov: this.fov,
    };
  }
  destroy() {
    this.stopMoving();
    this.events.abort();
    if (this.addedTabIndex && this.canvas.getAttribute("tabindex") === "0")
      this.canvas.removeAttribute("tabindex");
  }
}
