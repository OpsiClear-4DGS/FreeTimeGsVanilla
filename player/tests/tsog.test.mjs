import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { deflateRawSync } from "node:zlib";
import { crc32, openZip } from "../zip.js";
import { decodeTSOG, validateTSOGMetadata } from "../tsog.js";
import { sortVisible } from "../sort.js";

function fixture({ bands = 0, discrete = false, motionBits = 16, stationary = false } = {}) {
  const images = {};
  const img = (name, pixels, width = 4, height = 1) => {
    images[name] = { width, height, rgba: Uint8Array.from(pixels.flat()) };
    return name;
  };
  const solid = (name, value) => img(name, Array.from({ length: 4 }, () => value));
  const meta = {
    version: 4, count: 4,
    means: { mins: [-Math.log(2), 0, -Math.log(4)], maxs: [Math.log(2), 0, -Math.log(4)], files: [
      img("pl", [[0,0,0,255], [85,0,0,255], [170,0,0,255], [255,0,0,255]]),
      img("ph", [[0,0,0,255], [85,0,0,255], [170,0,0,255], [255,0,0,255]]),
    ] },
    scales: { codebook: [Math.log(0.1), Math.log(0.2), Math.log(0.3)], files: [solid("scales", [0,1,2,255])] },
    quats: { files: [img("quats", [252,253,254,255].map(tag => [128,128,128,tag]))] },
    sh0: { codebook: [-0.5,0,0.5], files: [solid("dc", [2,1,0,127])] },
  };
  if (!stationary) {
    meta.timeline = discrete ? {type:0, N:4, delta:16384} : {type:1, mins:[0,0.25], maxs:[1,0.5]};
    meta.timeline.files = [solid("tl", [0,0,0,255]), img("th", [32,96,160,224].map(v => [v,128,0,255]))];
    meta.temporal = {means:{mins:[[-2,1,0]], maxs:[[2,1,1]], files:[solid("vl", [255,0,0,255])]}};
    if (motionBits === 16) meta.temporal.means.files.push(solid("vh", [0,0,255,255]));
  }
  if (bands) {
    const rest = (bands + 1) ** 2 - 1;
    meta.shN = {bands, count:2, codebook:[-0.2,0.4,0.7], files:[
      img("centroids", Array.from({length:64*rest}, (_, i) => i < rest ? [0,1,2,255] : [2,0,1,255]), 64*rest, 1),
      img("labels", [0,1,1,0].map(v => [v,0,0,255])),
    ]};
  }
  return {meta, images, load: async name => {
    if (!images[name]) throw new Error("Missing image " + name);
    return images[name];
  }};
}
const identity = new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
const near = (a, b, tolerance = 1e-6) => assert.ok(Math.abs(a-b) < tolerance, `${a} != ${b}`);

test("TSOG reconstructs signed-log positions, covariance, DC, alpha and continuous motion", async () => {
  const {meta, images, load} = fixture();
  const progress = [];
  const model = await decodeTSOG(meta, load, {onProgress: value => progress.push(value)});
  assert.equal(model.format, "tsog");
  near(model.positionTime[0], -1);
  near(model.positionTime[12], 1);
  near(model.positionTime[2], -3);
  near(model.positionTime[3], 8192/65535);
  near(model.velocityDuration[3], 0.25+0.25*32768/65535);
  near(model.velocityDuration[0], -2+4*255/65535);
  near(model.velocityDuration[1], 1);
  near(model.velocityDuration[2], 65280/65535);
  near(model.covarianceA[0], 0.01, 0.00001);
  near(model.covarianceA[3], 0.04, 0.00001);
  near(model.covarianceB[1], 0.09, 0.00001);
  near(model.alpha[0], 127/255);
  assert.deepEqual([...model.sh.slice(0,4)], [0.5,0,-0.5,0]);
  assert.deepEqual(progress, [...progress].sort((a,b) => a-b));
  assert.equal(progress.at(-1), 1);
  const sampled = await decodeTSOG(meta, load, {maxPoints:2});
  assert.equal(sampled.count, 2);
  assert.equal(sampled.sourceCount, 4);
  assert.equal(sampled.positionTime[4], model.positionTime[8]);
  // Quarter-turn rotations exercise all four omitted-component tags. Pure axis
  // unit quaternions would leave diagonal covariance unchanged and miss swaps.
  images.quats.rgba.set([128,128,255,252, 255,128,128,253, 255,128,128,254, 255,128,128,255]);
  const rotated = await decodeTSOG(meta, load);
  for (const [i, expected] of [[0,[0.04,0.01,0.09]], [1,[0.01,0.09,0.04]], [2,[0.09,0.04,0.01]], [3,[0.04,0.01,0.09]]]) {
    near(rotated.covarianceA[i*4], expected[0], 0.00001);
    near(rotated.covarianceA[i*4+3], expected[1], 0.00001);
    near(rotated.covarianceB[i*4+1], expected[2], 0.00001);
  }
});

test("TSOG decodes 8-bit motion and SH palettes for degrees 1, 2 and 3", async () => {
  for (const bands of [1,2,3]) {
    const {meta, load} = fixture({bands, motionBits:8});
    const model = await decodeTSOG(meta, load);
    near(model.velocityDuration[0], 2);
    near(model.velocityDuration[2], 0);
    const stride = model.coefficients * 4;
    for (let c=1; c<model.coefficients; c++) {
      [-0.2,0.4,0.7].forEach((v,a) => near(model.sh[c*4+a], v));
      [0.7,-0.2,0.4].forEach((v,a) => near(model.sh[stride+c*4+a], v));
    }
  }
});

test("discrete sorting isolates each frame; static splats never fade or move", async () => {
  const discrete = fixture({discrete:true});
  const model = await decodeTSOG(discrete.meta, discrete.load);
  assert.equal(model.nFrames, 4);
  assert.equal(model.useVelocity, false);
  for (let frame=0; frame<4; frame++) {
    assert.equal(model.positionTime[frame*4+3], frame);
    assert.deepEqual([...sortVisible(model, frame, identity)], [frame]);
  }
  const stationary = fixture({stationary:true});
  const still = await decodeTSOG(stationary.meta, stationary.load);
  assert.equal(still.nFrames, 1);
  for (const time of [0,1,1000]) assert.deepEqual([...sortVisible(still, time, identity)], [0,1,2,3]);
});

test("TSOG rejects incompatible metadata before loading images", async () => {
  const edits = [
    m => m.version = 5, m => m.count = 0, m => m.means.mins[0] = NaN,
    m => m.quats.files = [], m => m.scales.codebook = [],
    m => m.sh0.codebook[0] = Infinity, m => m.timeline.type = 2,
    m => m.timeline.mins[1] = 0, m => m.temporal.scales = {},
    m => m.temporal.means.mins.push([0,0,0]), m => m.fps = -1,
    m => m.shN = {bands:4, count:1, codebook:[0], files:["a","b"]},
    m => m.timeline = {type:0,N:3,delta:1,files:["a","b"]},
  ];
  for (const edit of edits) {
    const {meta} = fixture(); edit(meta);
    assert.throws(() => validateTSOGMetadata(meta), /Invalid TSOG/);
  }
  const {meta} = fixture();
  await assert.rejects(decodeTSOG(meta, () => assert.fail("must not decode"), {maxPoints:0}), /Point limit/);
});

test("TSOG rejects corrupt images and supports cancellation between decoding stages", async () => {
  const edits = [
    f => f.images.quats.rgba[3] = 251,
    f => f.images.dc.rgba[0] = 255,
    f => f.images.ph.width = 3,
    f => f.images.labels.rgba[0] = 3,
    f => {f.images.ph.width = 2; f.images.ph.height = 2;},
    f => f.meta.means.maxs[0] = 1000,
  ];
  for (const edit of edits) {
    const f = fixture({bands:1}); edit(f);
    await assert.rejects(decodeTSOG(f.meta, f.load), /Invalid TSOG/);
  }
  const f = fixture(), controller = new AbortController();
  await assert.rejects(decodeTSOG(f.meta, f.load, {signal:controller.signal, onProgress:() => controller.abort()}), {name:"AbortError"});
});

function zipEntry(name, raw, method = 0) {
  const label = Buffer.from(name), packed = method ? deflateRawSync(raw) : raw;
  const local = Buffer.alloc(30), central = Buffer.alloc(46), end = Buffer.alloc(22);
  local.writeUInt32LE(0x04034b50); local.writeUInt16LE(method,8); local.writeUInt16LE(label.length,26);
  local.writeUInt32LE(crc32(raw),14); local.writeUInt32LE(packed.length,18); local.writeUInt32LE(raw.length,22);
  central.writeUInt32LE(0x02014b50); central.writeUInt16LE(method,10); central.writeUInt16LE(label.length,28);
  central.writeUInt32LE(crc32(raw),16); central.writeUInt32LE(packed.length,20); central.writeUInt32LE(raw.length,24);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(1,8); end.writeUInt16LE(1,10);
  end.writeUInt32LE(central.length+label.length,12); end.writeUInt32LE(local.length+label.length+packed.length,16);
  return Buffer.concat([local,label,packed,central,label,end]);
}

test("ZIP stored/deflated entries, CRC, size limits, truncation and cancellation", async () => {
  const raw = Buffer.from("123456789");
  assert.equal(crc32(raw), 0xcbf43926);
  for (const method of [0,8]) {
    const zip = await openZip(new Blob([zipEntry("attribute",raw,method)]));
    assert.deepEqual([...await zip.read("attribute")], [...raw]);
    await assert.rejects(zip.read("missing"), /missing/);
    await assert.rejects(zip.read("attribute",8), /entry size/);
  }
  const corrupt = zipEntry("a",raw); corrupt[31] ^= 1;
  await assert.rejects((await openZip(new Blob([corrupt]))).read("a"), /checksum/);
  await assert.rejects(openZip(new Blob([corrupt.slice(0,-4)])), /end-of-directory/);
  const expanded = zipEntry("a",raw,8); expanded.writeUInt32LE(1,expanded.length-22-47+24);
  await assert.rejects((await openZip(new Blob([expanded]))).read("a"), /expanded size/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(openZip(new Blob([corrupt]),controller.signal), {name:"AbortError"});
});

test("original encoder bundles with streamed ZIP descriptors load their v4 metadata", async () => {
  for (const name of ["continuous8", "continuous16", "discrete", "static"]) {
    const bytes = await readFile(new URL(`./fixtures/tsog/${name}.tsog`, import.meta.url));
    const archive = await openZip(new Blob([bytes]));
    const meta = validateTSOGMetadata(JSON.parse(new TextDecoder().decode(await archive.read("meta.json"))));
    assert.equal(meta.count, 256);
    if (name === "discrete") assert.equal(meta.fps,24);
    for (const group of [meta.means,meta.scales,meta.quats,meta.sh0,meta.timeline,meta.shN,meta.temporal?.means].filter(Boolean))
      for (const file of group.files) assert.equal(new TextDecoder().decode((await archive.read(file)).slice(0,4)), "RIFF");
  }
});
