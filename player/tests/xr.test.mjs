import { test } from "node:test";
import assert from "node:assert/strict";
import { inversePlacement, multiply4, placementMatrix, previewPose, xrCamera } from "../xr.js";

const identity = new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
const close = (a, b) => {
  assert.equal(a.length, b.length);
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 2e-5, `${a} != ${b}`));
};
const point = (m, p) => [0,1,2].map(row => m[row] * p[0] + m[4+row] * p[1] + m[8+row] * p[2] + m[12+row]);
const bounds = { center: [10,20,30], min: [8,18,26], radius: 5 };

test("AR placement centers arbitrary coordinates and rests Y/Z-up scenes on the surface", () => {
  const pose = new Float32Array([0,0,-1,0, 0,1,0,0, 1,0,0,0, 1,2,3,1]);
  for (const up of ["y", "z"]) {
    const centered = placementMatrix(bounds, up, 2, pose);
    close(point(centered, bounds.center), [1,2,3]);
    close(multiply4(inversePlacement(centered), centered), identity);
    const bottom = [...bounds.center];
    bottom[up === "y" ? 1 : 2] = bounds.min[up === "y" ? 1 : 2];
    const grounded = placementMatrix(bounds, up, 2, pose, true);
    close(point(grounded, bottom), [1,2,3]);
    const top = [...bottom];
    top[up === "y" ? 1 : 2] += 1;
    close(point(grounded, top), [1,2.2,3]);
  }
});

test("XR cameras compose scene scale and rotation while keeping SH eyes in model coordinates", () => {
  const model = placementMatrix(bounds, "z", 3, identity);
  for (const x of [-0.032, 0.032]) {
    const world = new Float32Array(identity);
    world[12] = x; world[13] = 1.6; world[14] = 2;
    const projection = new Float32Array(identity);
    projection[8] = x; // Eye-specific, asymmetric projection is preserved verbatim.
    const camera = xrCamera({ matrix: world, inverse: { matrix: inversePlacement(world) } }, projection, model);
    close(point(model, camera.eye), [x,1.6,2]);
    close(point(camera.view, camera.eye), [0,0,0]);
    close(point(camera.view, bounds.center), [-x,-1.6,-2]);
    assert.equal(camera.projection, projection);
    assert.equal(camera.near, 0.01);
  }
});

test("floating placement is 1.5 meters ahead of the viewer and remains upright", () => {
  const viewer = new Float32Array([0,0,-1,0, -0.6,0.8,0,0, 0.8,0.6,0,0, 3,4,5,1]);
  const pose = previewPose(viewer);
  close(pose.slice(12,15), [1.8,3.1,5]);
  close(pose.slice(4,7), [0,1,0]);
  close(multiply4(inversePlacement(pose), pose), identity);
});
