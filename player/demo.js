// A synthetic, moving ribbon encoded through the same public FTGS file layout.
const C0 = 0.28209479177387814;
function point(theta, around, t) {
  const phase = t * 2 * Math.PI,
    r = 1.6 + 0.6 * Math.cos(3 * theta + phase);
  return [
    (r + 0.12 * Math.cos(around)) * Math.cos(2 * theta),
    0.65 * Math.sin(3 * theta + phase) + 0.12 * Math.sin(around),
    (r + 0.12 * Math.cos(around)) * Math.sin(2 * theta),
  ];
}
export function demoFile() {
  const names = [
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
    "time",
    "log_duration",
    "velocity_0",
    "velocity_1",
    "velocity_2",
  ];
  const count = 17 * 256 * 10,
    data = new ArrayBuffer(count * names.length * 4),
    view = new DataView(data);
  let index = 0;
  for (let frame = 0; frame < 17; frame++)
    for (let ring = 0; ring < 256; ring++)
      for (let side = 0; side < 10; side++) {
        const theta = (ring / 256) * 2 * Math.PI,
          around = (side / 10) * 2 * Math.PI,
          time = frame / 16;
        const position = point(theta, around, time),
          next = point(theta, around, time + 0.001),
          prev = point(theta, around, time - 0.001);
        const blend = (Math.sin(theta) + 1) / 2;
        const rgb = [
          0.13 + 0.78 * blend ** 4,
          0.65 + 0.2 * (1 - blend),
          0.82 - 0.48 * blend,
        ];
        const row = [
          ...position,
          ...rgb.map((c) => (c - 0.5) / C0),
          0.85,
          Math.log(0.032),
          Math.log(0.032),
          Math.log(0.032),
          1,
          0,
          0,
          0,
          time,
          Math.log(0.035),
          ...next.map((p, i) => (p - prev[i]) / 0.002),
        ];
        for (const value of row) {
          view.setFloat32(index, value, true);
          index += 4;
        }
      }
  const header = [
    "ply",
    "format binary_little_endian 1.0",
    "comment ftgs_version 1",
    "comment time_units normalized",
    "comment sh_degree 0",
    "comment use_velocity 1",
    "comment min_duration 0.02",
    "comment opacity_floor 0.0001",
    "comment n_frames 181",
    `element vertex ${count}`,
    ...names.map((n) => `property float ${n}`),
    "end_header",
    "",
  ].join("\n");
  return new Blob([header, data], { type: "application/octet-stream" });
}
