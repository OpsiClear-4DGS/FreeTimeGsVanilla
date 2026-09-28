// Repository-owned TSOG v4 decoder, based on Xiaomi Research's format/encoder.
// Paper, original repository, compatibility notes and licenses: TSOG.md and THIRD_PARTY.md.
import { boundsForModel, covarianceFromQuaternion } from "./ftgs.js?v=6";
import { openZip } from "./zip.js?v=6";
import { AttributeImages } from "./webp.js?v=7";
import {
  validatePlaybackMetadata,
  audioMimeType,
  MAX_AUDIO_BYTES,
  isPackagePath,
  resolvePlayback,
} from "./playback.js?v=7";

export const TSOG_PROFILE = Object.freeze({
  id: "org.opsiclear.tsog-playback",
  version: 1,
});

const invalid = (message) => {
  throw new Error(`Invalid TSOG: ${message}`);
};
const positiveInteger = (value, max = Number.MAX_SAFE_INTEGER) =>
  Number.isSafeInteger(value) && value > 0 && value <= max;
const object = (value) => value && typeof value === "object" && !Array.isArray(value);
const float32 = (value) => typeof value === "number" && Number.isFinite(value) &&
  Math.abs(value) <= 3.4028234663852886e38;
const validateFiles = (group, sizes, name) => {
  if (
    !object(group) ||
    !Array.isArray(group.files) ||
    !sizes.includes(group.files.length) ||
    group.files.some((file) => !isPackagePath(file) || file === "meta.json") ||
    new Set(group.files).size !== group.files.length
  )
    invalid(`${name} has missing or invalid image filenames.`);
};
const validateRanges = (mins, maxs, count, name) => {
  if (
    !Array.isArray(mins) ||
    !Array.isArray(maxs) ||
    mins.length !== count ||
    maxs.length !== count ||
    mins.some(
      (min, i) =>
        !float32(min) || !float32(maxs[i]) || min > maxs[i],
    )
  )
    invalid(`${name} has invalid attribute ranges.`);
};
const validateCodebook = (group, name) => {
  if (
    !Array.isArray(group.codebook) ||
    !positiveInteger(group.codebook.length, 256) ||
    group.codebook.some((value) => !float32(value))
  )
    invalid(`${name} has an invalid codebook.`);
};

const lookup = (book, index) => {
  if (index >= book.length)
    invalid("an image references a missing codebook entry.");
  return book[index];
};
const finite = (value) => {
  if (!Number.isFinite(Math.fround(value)))
    invalid("an attribute exceeds float32 range.");
  return value;
};

function unpackQuaternion(bytes, offset) {
  const omitted = bytes[offset + 3] - 252;
  if (omitted < 0 || omitted > 3)
    invalid("invalid smallest-three quaternion tag.");
  const quaternion = [0, 0, 0, 0];
  let channel = 0;
  let squared = 0;
  for (let component = 0; component < 4; component++) {
    if (component === omitted) continue;
    const value = ((bytes[offset + channel++] / 255) * 2 - 1) / Math.SQRT2;
    quaternion[component] = value;
    squared += value * value;
  }
  quaternion[omitted] = Math.sqrt(Math.max(0, 1 - squared));
  return quaternion;
}

export function validateTSOGMetadata(meta, { requireProfile = false } = {}) {
  if (!object(meta) || meta.version !== 4)
    invalid("only the original exporter's version 4 is supported.");
  if (meta.profile !== undefined) {
    if (!object(meta.profile) || meta.profile.id !== TSOG_PROFILE.id ||
        meta.profile.version !== TSOG_PROFILE.version)
      invalid("unsupported TSOG playback profile or profile version.");
  } else if (requireProfile) invalid("an explicit TSOG playback profile is required.");
  if (!positiveInteger(meta.count))
    invalid("count must be a positive integer.");
  validateFiles(meta.means, [2], "means");
  validateRanges(meta.means.mins, meta.means.maxs, 3, "means");
  for (const group of ["scales", "sh0"]) {
    validateFiles(meta[group], [1], group);
    validateCodebook(meta[group], group);
  }
  validateFiles(meta.quats, [1], "quats");
  if (meta.shN !== undefined) {
    validateFiles(meta.shN, [2], "shN");
    validateCodebook(meta.shN, "shN");
    if (
      !positiveInteger(meta.shN.bands, 3) ||
      !positiveInteger(meta.shN.count, 65536)
    )
      invalid("SH bands or palette count are unsupported.");
  }
  if (meta.fps !== undefined && (!Number.isFinite(meta.fps) || meta.fps <= 0))
    invalid("fps must be positive.");
  if (meta.timeline !== undefined) {
    const timeline = meta.timeline;
    validateFiles(timeline, [2], "timeline");
    if (timeline.type === 1) {
      // The v4 exporter uses type 1 for center/scale; see TSOG.md.
      validateRanges(timeline.mins, timeline.maxs, 2, "timeline");
      if (!(Math.fround(timeline.mins[1]) > 0))
        invalid("temporal scales must be positive standard deviations.");
    } else if (timeline.type === 0) {
      if (
        !positiveInteger(timeline.N, 65536) ||
        timeline.delta !== Math.floor(65536 / timeline.N)
      )
        invalid(
          "discrete timeline has an invalid frame count or quantization step.",
        );
    } else invalid(`unsupported timeline type ${timeline.type}.`);
  }
  if (meta.temporal !== undefined) {
    if (
      !object(meta.temporal) ||
      Object.keys(meta.temporal).some((key) => key !== "means")
    )
      invalid("only first-order translation motion is supported.");
    const motion = meta.temporal.means;
    validateFiles(motion, [1, 2], "temporal.means");
    if (!Array.isArray(motion.mins) || !Array.isArray(motion.maxs) ||
        motion.mins.length !== 1 || motion.maxs.length !== 1)
      invalid("only first-order translation motion is supported.");
    validateRanges(motion.mins[0], motion.maxs[0], 3, "temporal.means");
    if (!meta.timeline) invalid("motion requires a timeline.");
  }
  validatePlaybackMetadata(meta);
  try {
    resolvePlayback({
      timelineMode: !meta.timeline ? 2 : meta.timeline.type === 0 ? 1 : 0,
      nFrames: !meta.timeline ? 1 : meta.timeline.type === 0 ? meta.timeline.N : null,
      fps: meta.fps,
      playback: meta.playback,
    });
  } catch (error) { invalid(error.message); }
  return meta;
}

export async function readTSOGMetadata(archive) {
  let meta;
  try {
    meta = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(
      await archive.read("meta.json", 4 * 1024 * 1024),
    ));
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError)
      invalid("meta.json must be UTF-8 JSON.");
    throw error;
  }
  return validateTSOGMetadata(meta);
}

/** Decode attribute images supplied by an async filename -> { width, height, rgba } callback. */
export async function decodeTSOG(
  meta,
  loadImage,
  { maxPoints = 1000000, signal, onProgress = () => {} } = {},
) {
  signal?.throwIfAborted();
  validateTSOGMetadata(meta);
  if (maxPoints !== Infinity && !positiveInteger(maxPoints))
    throw new Error("Point limit must be a positive integer.");
  const count = Math.min(meta.count, maxPoints);
  const degree = meta.shN?.bands ?? 0;
  const coefficients = (degree + 1) ** 2;
  // Internal modes: 0 = continuous Gaussian, 1 = discrete frame, 2 = static.
  const timelineMode = !meta.timeline ? 2 : meta.timeline.type === 0 ? 1 : 0;
  const model = {
    format: "tsog",
    timelineMode,
    count,
    sourceCount: meta.count,
    degree,
    coefficients,
    nFrames:
      timelineMode === 1 ? meta.timeline.N : timelineMode === 2 ? 1 : null,
    fps: meta.fps ?? null,
    playback: meta.playback ? { ...meta.playback } : null,
    useVelocity: timelineMode === 0 && Boolean(meta.temporal),
    opacityFloor: 0,
    positionTime: new Float32Array(count * 4),
    velocityDuration: new Float32Array(count * 4),
    covarianceA: new Float32Array(count * 4),
    covarianceB: new Float32Array(count * 4),
    sh: new Float32Array(count * coefficients * 4),
    alpha: new Float32Array(count),
  };
  let dimensions;
  const image = async (name, gaussian = true) => {
    signal?.throwIfAborted();
    const result = await loadImage(name);
    signal?.throwIfAborted();
    const { width, height, rgba } = result;
    if (
      !positiveInteger(width) ||
      !positiveInteger(height) ||
      !(rgba instanceof Uint8Array || rgba instanceof Uint8ClampedArray) ||
      rgba.length !== width * height * 4
    )
      invalid(`invalid RGBA image '${name}'.`);
    if (gaussian) {
      if (width * height < meta.count) invalid(`too few pixels in '${name}'.`);
      dimensions ??= [width, height];
      if (width !== dimensions[0] || height !== dimensions[1])
        invalid(`mismatched image size in '${name}'.`);
    }
    return result;
  };
  const pair = async (group) => {
    const low = await image(group.files[0]);
    const high = group.files[1] ? await image(group.files[1]) : null;
    return [low.rgba, high?.rgba];
  };
  const rows = async (operation, progress) => {
    for (let first = 0; first < count; first += 16384) {
      signal?.throwIfAborted();
      for (let i = first; i < Math.min(first + 16384, count); i++)
        operation(i, 4 * Math.floor((i * meta.count) / count), i * 4);
      // Let cancellation, navigation, and the loading UI run between chunks.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    signal?.throwIfAborted();
    onProgress(progress);
  };
  const value16 = (low, high, i, min, max) =>
    min + ((max - min) * (low[i] + 256 * high[i])) / 65535;
  {
    const [low, high] = await pair(meta.means);
    await rows((i, s, o) => {
      for (let a = 0; a < 3; a++) {
        const log = value16(
          low,
          high,
          s + a,
          meta.means.mins[a],
          meta.means.maxs[a],
        );
        model.positionTime[o + a] = finite(
          Math.sign(log) * Math.expm1(Math.abs(log)),
        );
      }
      model.velocityDuration[o + 3] = 1;
    }, 0.2);
  }
  {
    const scales = (await image(meta.scales.files[0])).rgba;
    const quats = (await image(meta.quats.files[0])).rgba;
    await rows((i, s, o) => {
      const covariance = covarianceFromQuaternion(
        unpackQuaternion(quats, s),
        [0, 1, 2].map((a) => lookup(meta.scales.codebook, scales[s + a])),
      ).map(finite);
      model.covarianceA.set(covariance.slice(0, 4), o);
      model.covarianceB.set(covariance.slice(4), o);
    }, 0.4);
  }
  {
    const colors = (await image(meta.sh0.files[0])).rgba;
    await rows((i, s, o) => {
      for (let a = 0; a < 3; a++)
        model.sh[i * coefficients * 4 + a] = lookup(
          meta.sh0.codebook,
          colors[s + a],
        );
      model.alpha[i] = model.covarianceB[o + 2] = colors[s + 3] / 255;
    }, 0.5);
  }
  if (meta.timeline) {
    const [low, high] = await pair(meta.timeline);
    const timeline = meta.timeline;
    await rows((i, s, o) => {
      if (timelineMode === 1) {
        model.positionTime[o + 3] = Math.min(
          timeline.N - 1,
          Math.floor((low[s] + 256 * high[s]) / timeline.delta),
        );
      } else {
        model.positionTime[o + 3] = finite(
          value16(low, high, s, timeline.mins[0], timeline.maxs[0]),
        );
        model.velocityDuration[o + 3] = finite(
          value16(low, high, s + 1, timeline.mins[1], timeline.maxs[1]),
        );
      }
    }, 0.65);
  }
  if (meta.temporal) {
    const motion = meta.temporal.means;
    const [low, high] = await pair(motion);
    await rows((i, s, o) => {
      for (let a = 0; a < 3; a++) {
        const min = motion.mins[0][a],
          max = motion.maxs[0][a];
        const value = high
          ? value16(low, high, s + a, min, max)
          : min + ((max - min) * low[s + a]) / 255;
        finite(value);
        if (model.useVelocity) model.velocityDuration[o + a] = value;
      }
    }, 0.75);
  }
  if (meta.shN) {
    const palette = await image(meta.shN.files[0], false);
    const labels = (await image(meta.shN.files[1])).rgba;
    const rest = coefficients - 1;
    if (
      palette.width !== 64 * rest ||
      palette.height !== Math.ceil(meta.shN.count / 64)
    )
      invalid("invalid SH centroid image size.");
    await rows((i, s) => {
      const label = labels[s] + 256 * labels[s + 1];
      if (label >= meta.shN.count)
        invalid("an SH label exceeds the palette count.");
      for (let c = 0; c < rest; c++)
        for (let a = 0; a < 3; a++)
          model.sh[(i * coefficients + c + 1) * 4 + a] = lookup(
            meta.shN.codebook,
            palette.rgba[(label * rest + c) * 4 + a],
          );
    }, 0.95);
  }
  model.bounds = boundsForModel(model.positionTime);
  signal?.throwIfAborted();
  onProgress(1);
  return model;
}

/** Read one packaged .tsog ZIP. Attribute decoding uses browser WebP and WebGL2. */
export async function readTSOG(blob, options = {}) {
  const archive = await openZip(blob, options.signal);
  const meta = await readTSOGMetadata(archive);
  const images = new AttributeImages();
  try {
    validateTSOGMetadata(meta);
    const audio = meta.audio
      ? {
          ...meta.audio,
          blob: new Blob(
            [await archive.read(meta.audio.file, MAX_AUDIO_BYTES)],
            {
              type: meta.audio.mimeType ?? audioMimeType(meta.audio.file),
            },
          ),
        }
      : null;
    if (audio && !audio.blob.size) invalid("the embedded audio file is empty.");
    const model = await decodeTSOG(
      meta,
      async (name) => images.decode(await archive.read(name), options.signal),
      options,
    );
    return { ...model, audio };
  } finally {
    images.destroy();
  }
}
