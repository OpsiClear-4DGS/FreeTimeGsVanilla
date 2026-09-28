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

export class OrbitCamera {
  constructor(canvas, onChange) {
    this.canvas = canvas;
    this.onChange = onChange;
    this.upAxis = "y";
    this.bounds = { center: [0, 0, 0], radius: 1 };
    this.fit();
    this.events = new AbortController();
    const options = { signal: this.events.signal };
    const pointers = new Map();
    canvas.addEventListener("contextmenu", (e) => e.preventDefault(), options);
    canvas.addEventListener(
      "pointerdown",
      (e) => {
        canvas.focus();
        canvas.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      },
      options,
    );
    canvas.addEventListener(
      "pointermove",
      (e) => {
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
        e.preventDefault();
        this.zoom(Math.exp(e.deltaY * 0.001));
        this.onChange();
      },
      { ...options, passive: false },
    );
  }
  fit(bounds = this.bounds) {
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
  restore({ eye, target, up = "y", fov = 45 }) {
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
    const offset = eye.map((v, i) => v - target[i]);
    const distance = Math.hypot(...offset);
    if (!(distance > 1e-6 && Number.isFinite(distance)))
      throw new Error(
        "Camera eye and target must be distinct finite positions.",
      );
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
    this.events.abort();
  }
}
