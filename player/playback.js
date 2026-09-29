// Optional player metadata in TSOG's root meta.json. See TSOG.md.
export const MAX_AUDIO_BYTES = 128 * 1024 * 1024;

// ZIP entry names are case-sensitive literal paths, never URLs.
export const isPackagePath = (file) =>
  typeof file === "string" && file.length > 0 &&
  !/[\\\x00-\x1f:?#]/.test(file) &&
  !file.split("/").some((part) => !part || part === "." || part === "..");

const audioTypes = {
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  aac: "audio/aac",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  opus: "audio/ogg",
  wav: "audio/wav",
  flac: "audio/flac",
  webm: "audio/webm",
};
export function audioMimeType(filename) {
  const dot = filename.lastIndexOf(".");
  const extension = dot < 0 ? "" : filename.slice(dot + 1).toLowerCase();
  return Object.hasOwn(audioTypes, extension) ? audioTypes[extension] : "";
}
export function validatePlaybackRate(rate) {
  if (!Number.isFinite(rate) || rate < 0.25 || rate > 4)
    throw new RangeError("Playback rate must be between 0.25 and 4.");
  return rate;
}
export function validateVolume(volume) {
  if (!Number.isFinite(volume) || volume < 0 || volume > 1)
    throw new RangeError("Volume must be between 0 and 1.");
  return volume;
}

export function validatePlaybackMetadata(meta) {
  const invalid = (message) => {
    throw new Error(`Invalid TSOG: ${message}`);
  };
  const object = (value) =>
    value && typeof value === "object" && !Array.isArray(value);
  const playback = meta.playback;
  if (playback !== undefined) {
    if (!object(playback)) invalid("playback must be an object.");
    for (const key of ["duration", "fps"])
      if (
        playback[key] !== undefined &&
        (!Number.isFinite(playback[key]) || playback[key] <= 0)
      )
        invalid(`playback.${key} must be positive.`);
    if (playback.rate !== undefined) {
      try {
        validatePlaybackRate(playback.rate);
      } catch (error) {
        invalid(error.message);
      }
    }
    if (playback.loop !== undefined && typeof playback.loop !== "boolean")
      invalid("playback.loop must be a boolean.");
    if (
      meta.timeline?.type === 0 &&
      playback.duration !== undefined &&
      playback.fps !== undefined &&
      Math.abs(playback.duration * playback.fps - meta.timeline.N) >
        1e-6 * meta.timeline.N
    )
      invalid(
        "discrete playback.duration must equal timeline.N / playback.fps.",
      );
  }
  const audio = meta.audio;
  if (audio !== undefined) {
    if (
      !object(audio) ||
      !isPackagePath(audio.file)
    )
      invalid("audio.file must name a file inside the package.");
    const attributes = [
      meta.means,
      meta.scales,
      meta.quats,
      meta.sh0,
      meta.shN,
      meta.timeline,
      meta.temporal?.means,
    ];
    if (
      audio.file === "meta.json" ||
      attributes.some((group) => group?.files?.includes(audio.file))
    )
      invalid("audio.file cannot replace metadata or attribute images.");
    if (
      audio.mimeType !== undefined &&
      (typeof audio.mimeType !== "string" ||
        !/^audio\/[a-z0-9.+-]+$/i.test(audio.mimeType))
    )
      invalid("audio.mimeType must be an audio MIME type.");
    if (!audio.mimeType && !audioMimeType(audio.file))
      invalid("audio requires a known extension or mimeType.");
    if (audio.volume !== undefined) {
      try {
        validateVolume(audio.volume);
      } catch (error) {
        invalid(error.message);
      }
    }
    if (!meta.timeline && !playback?.duration)
      invalid("a static scene with audio needs playback.duration.");
  }
}

/** Resolve file defaults and explicit canvas/query overrides in one place. */
export function resolvePlayback(data, options = {}) {
  const playback = data.playback ?? {};
  const discrete = data.timelineMode === 1;
  const nativeFps =
    discrete && playback.duration
      ? data.nFrames / playback.duration
      : (playback.fps ?? data.fps ?? 30);
  const fps = options.fps ?? nativeFps;
  const nFrames =
    data.timelineMode === 0
      ? (options.frames ??
        data.nFrames ??
        (playback.duration
          ? Math.max(2, Math.round(playback.duration * nativeFps) + 1)
          : 300))
      : data.nFrames;
  if (!Number.isSafeInteger(nFrames) || nFrames < 1)
    throw new RangeError("Playback metadata produces an invalid frame count.");
  const overrideTiming =
    data.timelineMode !== 2 &&
    (options.fps != null ||
      (data.timelineMode === 0 && options.frames != null));
  const duration =
    !overrideTiming && playback.duration !== undefined
      ? playback.duration
      : Math.max(1, nFrames - (discrete ? 0 : 1)) / fps;
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(fps) || fps <= 0)
    throw new RangeError("Playback metadata produces invalid timing.");
  return {
    nFrames,
    fps,
    duration,
    playable: nFrames > 1 || playback.duration !== undefined,
    playbackRate: options.playbackRate ?? playback.rate ?? 1,
    loop: options.loop ?? playback.loop ?? true,
  };
}
