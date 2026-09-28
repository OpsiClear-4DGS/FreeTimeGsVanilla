// Vanilla FreeTimeGS PLY version 1; see FORMAT.md for the complete layout.
const REQUIRED = [
  "x",
  "y",
  "z",
  "opacity",
  "time",
  "log_duration",
  ...[0, 1, 2].flatMap((i) => [`f_dc_${i}`, `scale_${i}`, `velocity_${i}`]),
  ...[0, 1, 2, 3].map((i) => `rot_${i}`),
];

export function parseHeader(text) {
  const end = /^end_header\r?\n/m.exec(text);
  if (!end)
    throw new Error("Missing PLY end_header (maximum header size is 1 MiB).");
  const lines = text.slice(0, end.index).split(/\r?\n/);
  if (lines.shift() !== "ply")
    throw new Error("Choose a binary .ftgs.ply exported by FreeTimeGS.");
  const metadata = {},
    fields = [];
  let count, format, element;
  for (const line of lines) {
    const [kind, ...parts] = line.trim().split(/\s+/);
    if (kind === "format") format = parts.join(" ");
    if (kind === "comment") metadata[parts[0]] = parts.slice(1).join(" ");
    if (kind === "element") {
      if (element || parts[0] !== "vertex")
        throw new Error("FTGS v1 expects one vertex element.");
      element = parts[0];
      count = Number(parts[1]);
    }
    if (kind === "property") {
      if (
        !element ||
        !["float", "float32"].includes(parts[0]) ||
        parts.length !== 2
      )
        throw new Error("FTGS v1 properties must be scalar float32 values.");
      if (fields.includes(parts[1]))
        throw new Error(`Duplicate PLY property: ${parts[1]}`);
      fields.push(parts[1]);
    }
  }
  if (format !== "binary_little_endian 1.0")
    throw new Error(
      "Use binary little-endian PLY, not ASCII or compressed PLY.",
    );
  if (metadata.ftgs_version !== "1" || metadata.time_units !== "normalized")
    throw new Error(
      "Expected FTGS version 1 with normalized time. Export using export_ftgs_ply.py.",
    );
  if (!Number.isSafeInteger(count) || count < 1)
    throw new Error("The model has no Gaussians or an invalid vertex count.");
  const degree = Number(metadata.sh_degree);
  if (!Number.isInteger(degree) || degree < 0 || degree > 3)
    throw new Error("This player supports SH degrees 0 through 3.");
  const coefficients = (degree + 1) ** 2;
  for (const name of [
    ...REQUIRED,
    ...Array.from({ length: 3 * (coefficients - 1) }, (_, i) => `f_rest_${i}`),
  ]) {
    if (!fields.includes(name))
      throw new Error(`Missing FTGS property: ${name}`);
  }
  if (!["0", "1"].includes(metadata.use_velocity))
    throw new Error("Missing or invalid use_velocity metadata.");
  const minDuration = Number(metadata.min_duration),
    opacityFloor = Number(metadata.opacity_floor);
  if (
    !(minDuration > 0 && Number.isFinite(minDuration)) ||
    !(opacityFloor >= 0 && opacityFloor < 1)
  )
    throw new Error("Invalid duration or opacity metadata.");
  const nFrames =
    metadata.n_frames === undefined ? null : Number(metadata.n_frames);
  if (nFrames !== null && (!Number.isSafeInteger(nFrames) || nFrames < 1))
    throw new Error("Invalid n_frames metadata.");
  return {
    sourceCount: count,
    degree,
    coefficients,
    useVelocity: metadata.use_velocity === "1",
    minDuration,
    opacityFloor,
    nFrames,
    fields,
    offsets: Object.fromEntries(fields.map((name, i) => [name, i * 4])),
    stride: fields.length * 4,
    dataOffset: end.index + end[0].length,
  };
}

export function covarianceFromQuaternion(q, logScales) {
  const norm = Math.hypot(...q);
  if (!(norm > 0)) throw new Error("A Gaussian has a zero quaternion.");
  const [w, x, y, z] = q.map((v) => v / norm);
  const R = [
    1 - 2 * (y * y + z * z),
    2 * (x * y - w * z),
    2 * (x * z + w * y),
    2 * (x * y + w * z),
    1 - 2 * (x * x + z * z),
    2 * (y * z - w * x),
    2 * (x * z - w * y),
    2 * (y * z + w * x),
    1 - 2 * (x * x + y * y),
  ];
  const s = logScales.map((v) => Math.exp(2 * v));
  return [
    [0, 0],
    [0, 1],
    [0, 2],
    [1, 1],
    [1, 2],
    [2, 2],
  ].map(
    ([a, b]) =>
      R[3 * a] * R[3 * b] * s[0] +
      R[3 * a + 1] * R[3 * b + 1] * s[1] +
      R[3 * a + 2] * R[3 * b + 2] * s[2],
  );
}

export function boundsForModel(positionTime) {
  // Fit the central 98% so a few triangulation outliers do not hide the scene.
  const axes = [[], [], []],
    step = Math.max(1, Math.floor(positionTime.length / 4 / 8192));
  for (let i = 0; i < positionTime.length / 4; i += step)
    for (let a = 0; a < 3; a++) axes[a].push(positionTime[4 * i + a]);
  const lo = [],
    hi = [];
  for (const values of axes) {
    values.sort((a, b) => a - b);
    lo.push(values[Math.floor((values.length - 1) * 0.01)]);
    hi.push(values[Math.ceil((values.length - 1) * 0.99)]);
  }
  return {
    min: lo,
    max: hi,
    center: lo.map((v, i) => (v + hi[i]) / 2),
    radius: Math.max(0.01, Math.hypot(...lo.map((v, i) => hi[i] - v)) / 2),
  };
}

export async function readFTGS(
  blob,
  { maxPoints = 1000000, signal, onProgress = () => {} } = {},
) {
  signal?.throwIfAborted();
  // Latin-1 keeps header character offsets identical to byte offsets.
  const text = new TextDecoder("latin1").decode(
    await blob.slice(0, 1024 * 1024).arrayBuffer(),
  );
  const header = parseHeader(text);
  if (header.dataOffset + header.sourceCount * header.stride > blob.size)
    throw new Error("The PLY file is truncated.");
  if (
    maxPoints !== Infinity &&
    (!Number.isSafeInteger(maxPoints) || maxPoints < 1)
  )
    throw new Error("Point limit must be a positive integer.");
  const count = Math.min(header.sourceCount, Math.floor(maxPoints));
  const model = {
    ...header,
    count,
    positionTime: new Float32Array(count * 4),
    velocityDuration: new Float32Array(count * 4),
    covarianceA: new Float32Array(count * 4),
    covarianceB: new Float32Array(count * 4),
    sh: new Float32Array(count * header.coefficients * 4),
    alpha: new Float32Array(count),
  };
  let output = 0;
  const rowsPerChunk = 16384;
  for (let first = 0; first < header.sourceCount; first += rowsPerChunk) {
    signal?.throwIfAborted();
    const last = Math.min(header.sourceCount, first + rowsPerChunk);
    const bytes = await blob
      .slice(
        header.dataOffset + first * header.stride,
        header.dataOffset + last * header.stride,
      )
      .arrayBuffer();
    const view = new DataView(bytes);
    while (output < count) {
      // Even spacing keeps samples across all keyframes, not just the file prefix.
      const source = Math.floor((output * header.sourceCount) / count);
      if (source >= last) break;
      const base = (source - first) * header.stride;
      const get = (name) => {
        const value = view.getFloat32(base + header.offsets[name], true);
        if (!Number.isFinite(value))
          throw new Error(`Non-finite ${name} at Gaussian ${source}.`);
        return value;
      };
      const o = output * 4;
      model.positionTime.set([get("x"), get("y"), get("z"), get("time")], o);
      const duration = Math.max(
        header.minDuration,
        Math.exp(get("log_duration")),
      );
      model.velocityDuration.set(
        [get("velocity_0"), get("velocity_1"), get("velocity_2"), duration],
        o,
      );
      const cov = covarianceFromQuaternion(
        [0, 1, 2, 3].map((i) => get(`rot_${i}`)),
        [0, 1, 2].map((i) => get(`scale_${i}`)),
      );
      const alpha = 1 / (1 + Math.exp(-get("opacity")));
      model.covarianceA.set(cov.slice(0, 4), o);
      model.covarianceB.set([cov[4], cov[5], alpha, 0], o);
      model.alpha[output] = alpha;
      for (
        let coefficient = 0;
        coefficient < header.coefficients;
        coefficient++
      ) {
        for (let channel = 0; channel < 3; channel++) {
          const field =
            coefficient === 0
              ? `f_dc_${channel}`
              : `f_rest_${channel * (header.coefficients - 1) + coefficient - 1}`;
          model.sh[(output * header.coefficients + coefficient) * 4 + channel] =
            get(field);
        }
      }
      if (
        ![
          ...model.covarianceA.subarray(o, o + 4),
          ...model.covarianceB.subarray(o, o + 2),
          model.velocityDuration[o + 3],
        ].every(Number.isFinite)
      )
        throw new Error(
          `Gaussian ${source} has scales or a duration outside the supported float range.`,
        );
      output++;
    }
    onProgress(last / header.sourceCount);
  }
  signal?.throwIfAborted();
  model.bounds = boundsForModel(model.positionTime);
  return model;
}
