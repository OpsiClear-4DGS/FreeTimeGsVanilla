import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHeader, readFTGS, covarianceFromQuaternion } from "../ftgs.js";
import { sortVisible } from "../sort.js";
import { lookAt, OrbitCamera } from "../camera.js";
import { demoFile } from "../demo.js";

function fixture({
  n = 4,
  degree = 1,
  reverse = false,
  metadata = "",
  edit = () => {},
} = {}) {
  let fields = [
    "x",
    "y",
    "z",
    "nx",
    "ny",
    "nz",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    ...Array.from(
      { length: 3 * ((degree + 1) ** 2 - 1) },
      (_, i) => `f_rest_${i}`,
    ),
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
  if (reverse) fields = fields.reverse();
  const header = [
    "ply",
    "format binary_little_endian 1.0",
    "comment ftgs_version 1",
    "comment time_units normalized",
    `comment sh_degree ${degree}`,
    "comment use_velocity 1",
    "comment min_duration 0.02",
    "comment opacity_floor 0.0001",
    metadata,
    `element vertex ${n}`,
    ...fields.map((f) => `property float ${f}`),
    "end_header",
    "",
  ].join("\n");
  const bytes = new ArrayBuffer(n * fields.length * 4),
    view = new DataView(bytes);
  for (let row = 0; row < n; row++) {
    const values = {
      x: row,
      y: row / 2,
      z: -row,
      opacity: 0,
      rot_0: 1,
      time: 0.5,
      log_duration: Math.log(0.1),
      scale_0: Math.log(1),
      scale_1: Math.log(2),
      scale_2: Math.log(3),
      velocity_0: 2,
    };
    fields
      .filter((f) => f.startsWith("f_rest_"))
      .forEach((f) => (values[f] = Number(f.slice(7)) + 1));
    edit(values, row);
    fields.forEach((field, i) =>
      view.setFloat32((row * fields.length + i) * 4, values[field] ?? 0, true),
    );
  }
  return { header, blob: new Blob([header, bytes]) };
}
const near = (actual, expected, eps = 1e-5) => {
  assert.equal(actual.length, expected.length);
  actual.forEach((v, i) =>
    assert.ok(Math.abs(v - expected[i]) < eps, `${i}: ${v} != ${expected[i]}`),
  );
};

test("bookmarked camera restores position and viewing direction with Y or Z up", () => {
  const camera = Object.create(OrbitCamera.prototype);
  camera.bounds = { radius: 1 };
  const eye = [-0.38, 0.16, 0.06],
    target = [-0.07, -0.39, -0.1];
  for (const up of ["y", "z"]) {
    camera.restore({ eye, target, up, fov: 38 });
    const snapshot = camera.snapshot();
    near(snapshot.eye, eye);
    near(
      [...snapshot.view],
      [...lookAt(eye, target, up === "z" ? [0, 0, 1] : [0, 1, 0])],
    );
    near([snapshot.fov], [(38 * Math.PI) / 180]);
  }
  assert.throws(() => camera.restore({ eye, target: eye }), /distinct/);
  assert.throws(
    () => camera.restore({ eye: [Infinity, 0, 0], target }),
    /Invalid camera/,
  );
  assert.throws(
    () => camera.restore({ eye, target, fov: 180 }),
    /Invalid camera/,
  );
});

test("FTGS fields are located by name and SH channels are reconstructed correctly", async () => {
  const model = await readFTGS(
    fixture({ reverse: true, metadata: "comment n_frames 61" }).blob,
  );
  assert.equal(model.count, 4);
  assert.equal(model.nFrames, 61);
  assert.equal(model.degree, 1);
  near([...model.positionTime.slice(4, 8)], [1, 0.5, -1, 0.5]);
  near([...model.velocityDuration.slice(0, 4)], [2, 0, 0, 0.1]);
  near([...model.covarianceA.slice(0, 4)], [1, 0, 0, 4]);
  near([...model.covarianceB.slice(0, 4)], [0, 9, 0.5, 0]);
  near([...model.sh.slice(4, 16)], [1, 4, 7, 0, 2, 5, 8, 0, 3, 6, 9, 0]);
});
test("higher SH bands, duration projection and absent frame count", async () => {
  const model = await readFTGS(
    fixture({ degree: 3, edit: (v) => (v.log_duration = -50) }).blob,
  );
  assert.equal(model.nFrames, null);
  assert.equal(model.coefficients, 16);
  assert.equal(model.sh[15 * 4], 15);
  assert.equal(model.sh[15 * 4 + 1], 30);
  assert.equal(model.sh[15 * 4 + 2], 45);
  near([model.velocityDuration[3]], [0.02]);
});
test("point limiting samples throughout the file and reports the source count", async () => {
  const model = await readFTGS(fixture({ n: 17 }).blob, { maxPoints: 3 });
  assert.equal(model.count, 3);
  assert.equal(model.sourceCount, 17);
  assert.deepEqual(
    [model.positionTime[0], model.positionTime[4], model.positionTime[8]],
    [0, 5, 11],
  );
});
test("incompatible schemas and missing fields fail before rendering", () => {
  const { header } = fixture();
  assert.throws(
    () => parseHeader(header.replace("ftgs_version 1", "ftgs_version 2")),
    /version 1/,
  );
  assert.throws(
    () => parseHeader(header.replace("binary_little_endian", "ascii")),
    /binary little-endian/,
  );
  assert.throws(
    () => parseHeader(header.replace("property float time\n", "")),
    /Missing FTGS property: time/,
  );
  assert.throws(
    () => parseHeader(header.replace("normalized", "seconds")),
    /normalized/,
  );
  assert.throws(
    () => parseHeader(header.replace("sh_degree 1", "sh_degree 4")),
    /degrees 0 through 3/,
  );
  assert.throws(
    () => parseHeader(header.replace("use_velocity 1", "use_velocity yes")),
    /use_velocity/,
  );
  assert.throws(
    () => parseHeader(header.replace("element vertex 4", "element vertex 0")),
    /no Gaussians/,
  );
});
test("truncation, non-finite data and zero quaternions fail cleanly", async () => {
  const { blob } = fixture();
  await assert.rejects(readFTGS(blob.slice(0, blob.size - 1)), /truncated/);
  await assert.rejects(
    readFTGS(fixture({ edit: (v) => (v.x = NaN) }).blob),
    /Non-finite x/,
  );
  await assert.rejects(
    readFTGS(fixture({ edit: (v) => (v.rot_0 = 0) }).blob),
    /zero quaternion/,
  );
});
test("a superseded load can be aborted", async () => {
  const abort = new AbortController();
  await assert.rejects(
    readFTGS(fixture().blob, {
      signal: abort.signal,
      onProgress: () => abort.abort(),
    }),
    { name: "AbortError" },
  );
});
test("anisotropic covariance follows wxyz quaternion rotation", () => {
  near(
    covarianceFromQuaternion(
      [Math.SQRT1_2, 0, 0, Math.SQRT1_2],
      [0, Math.log(2), Math.log(3)],
    ),
    [4, 0, 0, 1, 0, 9],
  );
});
test("depth sorting follows time-dependent motion and the velocity switch", () => {
  const model = {
    positionTime: new Float32Array([0, 0, 0, 0.5, 0, 0, 1, 0.5]),
    velocityDuration: new Float32Array([0, 0, 4, 1, 0, 0, -4, 1]),
    alpha: new Float32Array([1, 1]),
    useVelocity: true,
    opacityFloor: 0.0001,
  };
  const view = lookAt([0, 0, 10], [0, 0, 0], [0, 1, 0]);
  assert.deepEqual([...sortVisible(model, 0, view)], [0, 1]);
  assert.deepEqual([...sortVisible(model, 1, view)], [1, 0]);
  model.useVelocity = false;
  assert.deepEqual([...sortVisible(model, 1, view)], [0, 1]);
});
test("invisible and behind-camera Gaussians are excluded; equal depths stay stable", () => {
  const model = {
    positionTime: new Float32Array([
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 12, 0, 0, 0, 0, 9,
    ]),
    velocityDuration: new Float32Array([
      0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.02,
    ]),
    alpha: new Float32Array([1, 1, 1, 1]),
    useVelocity: false,
    opacityFloor: 0.0001,
  };
  assert.deepEqual(
    [...sortVisible(model, 0, lookAt([0, 0, 10], [0, 0, 0], [0, 1, 0]))],
    [0, 1],
  );
});
test("the built-in demo uses the actual FTGS parser and animated parameters", async () => {
  const model = await readFTGS(demoFile());
  assert.equal(model.count, 43520);
  assert.equal(model.degree, 0);
  assert.equal(model.nFrames, 181);
  assert.ok(model.velocityDuration.some((v) => Math.abs(v) > 1));
  assert.ok(model.bounds.radius > 1 && model.bounds.radius < 4);
});
