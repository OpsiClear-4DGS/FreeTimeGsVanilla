const ALPHA_CUTOFF = 1 / 255;

/** Prepare an immutable model once; each result owns a transferable order buffer. */
export function createSorter(model) {
  const count = model.alpha.length;
  const support = (model.timelineMode ?? 0) === 0 && model.opacityFloor < ALPHA_CUTOFF
    ? new Float64Array(count) : null;
  if (support) {
    for (let i = 0; i < count; i++) {
      const alpha = model.alpha[i], center = model.positionTime[i * 4 + 3],
        duration = model.velocityDuration[i * 4 + 3];
      // Invalid parameters take the reference path instead of being discarded.
      if (!Number.isFinite(alpha) || !Number.isFinite(center) ||
          !Number.isFinite(duration) || !(duration > 0)) {
        support[i] = Infinity;
      } else if (alpha < ALPHA_CUTOFF) support[i] = -1;
      else {
        // Include exp() rounding to one at the cutoff, even for wide durations.
        const extent = duration * Math.sqrt(2 * (Math.log(alpha / ALPHA_CUTOFF) + 8 * Number.EPSILON));
        // Widen the analytic interval against rounding. Candidates still use
        // the original exponential test; this never adds visible Gaussians.
        support[i] = extent + 1e-6 * Math.max(1, Math.abs(center), extent);
      }
    }
  }
  const workspace = {
    support,
    depths: new Float32Array(count),
    ids: new Uint32Array(count),
    keys: new Uint16Array(count),
    bins: new Uint32Array(65536),
  };
  return (time, view, near = 0.001) => sortVisible(model, time, view, near, workspace);
}

// Stable counting sort of visible, animated centers. No stale canonical-depth order.
export function sortVisible(model, time, view, near = 0.001, workspace = null) {
  const {
    positionTime: p,
    velocityDuration: v,
    alpha,
    useVelocity,
    opacityFloor,
    timelineMode = 0,
  } = model;
  const count = alpha.length,
    depths = workspace?.depths ?? new Float32Array(count),
    ids = workspace?.ids ?? new Uint32Array(count),
    support = opacityFloor < ALPHA_CUTOFF ? workspace?.support : null;
  let visible = 0,
    min = Infinity,
    max = -Infinity;
  for (let i = 0; i < count; i++) {
    const o = 4 * i;
    if (timelineMode === 1 && time !== p[o + 3]) continue;
    const dt = timelineMode === 0 ? time - p[o + 3] : 0;
    if (support && Math.abs(dt) > support[i]) continue;
    const opacity =
      timelineMode === 0
        ? Math.max(
            opacityFloor,
            alpha[i] * Math.exp(-0.5 * (dt / v[o + 3]) ** 2),
          )
        : alpha[i];
    if (opacity < ALPHA_CUTOFF) continue; // Same cutoff as the splat rasterizer.
    const motion = useVelocity ? dt : 0;
    const x = p[o] + v[o] * motion,
      y = p[o + 1] + v[o + 1] * motion,
      z = p[o + 2] + v[o + 2] * motion;
    const depth = -(view[2] * x + view[6] * y + view[10] * z + view[14]);
    if (!(depth > near && Number.isFinite(depth))) continue;
    depths[visible] = depth;
    ids[visible++] = i;
    min = Math.min(min, depth);
    max = Math.max(max, depth);
  }
  const bins = workspace?.bins.fill(0) ?? new Uint32Array(65536),
    keys = workspace?.keys ?? new Uint16Array(visible);
  const scale = max === min ? 0 : 65535 / (max - min);
  for (let i = 0; i < visible; i++) {
    const key = Math.max(
      0,
      Math.min(65535, Math.floor((max - depths[i]) * scale)),
    );
    keys[i] = key;
    bins[key]++;
  }
  let offset = 0;
  for (let i = 0; i < bins.length; i++) {
    const n = bins[i];
    bins[i] = offset;
    offset += n;
  }
  const order = new Uint32Array(visible);
  for (let i = 0; i < visible; i++) order[bins[keys[i]]++] = ids[i];
  return order;
}
