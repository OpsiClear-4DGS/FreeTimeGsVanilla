// Stable counting sort of visible, animated centers. No stale canonical-depth order.
export function sortVisible(model, time, view, near = 0.001) {
  const {
    positionTime: p,
    velocityDuration: v,
    alpha,
    useVelocity,
    opacityFloor,
  } = model;
  const count = alpha.length,
    depths = new Float32Array(count),
    ids = new Uint32Array(count);
  let visible = 0,
    min = Infinity,
    max = -Infinity;
  for (let i = 0; i < count; i++) {
    const o = 4 * i,
      dt = time - p[o + 3];
    const opacity = Math.max(
      opacityFloor,
      alpha[i] * Math.exp(-0.5 * (dt / v[o + 3]) ** 2),
    );
    if (opacity < 1 / 255) continue; // Same negligible-alpha cutoff as the splat rasterizer.
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
  const bins = new Uint32Array(65536),
    keys = new Uint16Array(visible);
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
