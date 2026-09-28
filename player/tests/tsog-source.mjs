// Neutral, synthetic attributes used only by the TSOG interoperability fixtures.
export const tsogColumns = [
  "x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2",
  ...Array.from({ length: 45 }, (_, i) => `f_rest_${i}`),
  "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
  "opacity", "t", "t_scale", "motion_0", "motion_1", "motion_2",
];
export function tsogRow(i, discrete = false) {
  const group = Math.floor(i / 64), local = i % 64;
  const row = {
    x: (local % 8 - 3.5) * 0.12 + (discrete ? group * 0.6 - 0.9 : 0),
    y: (Math.floor(local / 8) - 3.5) * 0.12,
    z: Math.sin(i * 0.3) * 0.08,
    opacity: i % 17 === 0 ? -5 : 1.5,
    t: discrete ? group : 0.25 + i / 510,
    t_scale: 0.18 + i / 2560,
  };
  for (let a = 0; a < 3; a++) {
    row[`f_dc_${a}`] = a === group % 3 ? 1.1 : -0.9 + i / 2550;
    row[`scale_${a}`] = -3.1 + a * 0.2 + (i % 8) * 0.01;
    row[`motion_${a}`] = discrete ? 0 : (a === 0 ? 1.8 : 0.1) * (i / 255 - 0.5);
  }
  for (let a = 0; a < 4; a++) row[`rot_${a}`] = a === i % 4 ? 1 : 0;
  for (let a = 0; a < 45; a++) row[`f_rest_${a}`] = Math.sin(i * 0.2 + a) * 0.03;
  return row;
}
