import { test } from "node:test";
import assert from "node:assert/strict";
import { OrbitCamera } from "../camera.js";

const close = (actual, expected) =>
  actual.forEach((value, i) =>
    assert.ok(Math.abs(value - expected[i]) < 1e-6, `${actual} != ${expected}`),
  );
function camera(up = "y", radius = 2, distance = 10) {
  const result = Object.create(OrbitCamera.prototype);
  result.bounds = { center: [0, 0, 0], radius };
  result.restore({
    eye: up === "y" ? [0, 0, distance] : [0, -distance, 0],
    target: [0, 0, 0],
    up,
  });
  return result;
}

test("translation follows the view and world up without changing the orbit angle", () => {
  for (const up of ["y", "z"]) {
    const c = camera(up);
    const start = c.snapshot();
    c.move(1, 0, 0, 0.1);
    close(c.target, up === "y" ? [0, 0, -0.2] : [0, 0.2, 0]);
    c.move(-1, 0, 0, 0.1);
    close(c.target, [0, 0, 0]);
    c.move(0, 1, 0, 0.1);
    close(c.target, [0.2, 0, 0]);
    c.move(0, -1, 0, 0.1);
    c.move(0, 0, 1, 0.1);
    close(c.target, up === "y" ? [0, 0.2, 0] : [0, 0, 0.2]);
    c.move(0, 0, -1, 0.1);
    close(c.snapshot().eye, start.eye);
    close([...c.snapshot().view.slice(0, 12)], [...start.view.slice(0, 12)]);
    assert.equal(c.distance, 10);
  }
  const c = camera();
  c.restore({ eye: [3, 4, 5], target: [0, 0, 0] });
  c.move(1, 0, 0, 0.1);
  close(
    c.target,
    [3, 4, 5].map((v) => (-v * 0.2) / Math.sqrt(50)),
  );
});

test("travel is frame-rate independent, normalized diagonally, and four times faster with Shift", () => {
  const slowFrames = camera(),
    fastFrames = camera(),
    sprint = camera();
  for (let i = 0; i < 30; i++) slowFrames.move(1, 0, 0, 1 / 30);
  for (let i = 0; i < 120; i++) fastFrames.move(1, 0, 0, 1 / 120);
  for (let i = 0; i < 60; i++) sprint.move(1, 0, 0, 1 / 60, true);
  close(slowFrames.target, [0, 0, -2]);
  close(fastFrames.target, slowFrames.target);
  close(sprint.target, [0, 0, -8]);
  const diagonal = camera();
  diagonal.move(1, 1, 1, 0.1);
  close([Math.hypot(...diagonal.target)], [0.2]);
});

test("travel adapts to scene scale and zoom, and stalled frames do not cause jumps", () => {
  const large = camera("y", 20, 100),
    closeUp = camera("y", 2, 0.5);
  large.move(1, 0, 0, 0.1);
  closeUp.move(1, 0, 0, 0.1);
  close(large.target, [0, 0, -2]);
  close(closeUp.target, [0, 0, -0.05]);
  const stalled = camera();
  stalled.move(1, 0, 0, 3);
  close(stalled.target, [0, 0, -0.2]);
  for (const elapsed of [0, -1, NaN, Infinity]) stalled.move(1, 0, 0, elapsed);
  stalled.move(0, 0, 0, 1 / 60);
  close(stalled.target, [0, 0, -0.2]);
});
