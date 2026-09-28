// Container validation needs only Node.js 22+ or a modern browser.
// Supply decodeImage for exhaustive attribute checks; audio codecs are not decoded.
import { openZip } from "./zip.js?v=8";
import { readTSOGMetadata, validateTSOGMetadata, decodeTSOG } from "./tsog.js?v=8";
import { inspectAttributeImage } from "./webp.js?v=7";
import { MAX_AUDIO_BYTES } from "./playback.js?v=8";

export async function validateTSOGPackage(blob, { signal, requireProfile = false, decodeImage } = {}) {
  const archive = await openZip(blob, signal);
  const meta = validateTSOGMetadata(await readTSOGMetadata(archive), { requireProfile });
  const groups = [meta.means, meta.scales, meta.quats, meta.sh0, meta.shN,
    meta.timeline, meta.temporal?.means].filter(Boolean);
  const names = new Set(groups.flatMap((group) => group.files));
  const headers = new Map();
  let dimensions;
  for (const group of groups) for (const [index, name] of group.files.entries()) {
    if (!headers.has(name)) headers.set(name, inspectAttributeImage(await archive.read(name)));
    const { width, height } = headers.get(name);
    if (group === meta.shN && index === 0) {
      if (width !== 64 * ((meta.shN.bands + 1) ** 2 - 1) || height !== Math.ceil(meta.shN.count / 64))
        throw new Error("Invalid TSOG: invalid SH centroid image size.");
    } else {
      dimensions ??= { width, height };
      if (width !== dimensions.width || height !== dimensions.height || width * height < meta.count)
        throw new Error(`Invalid TSOG: mismatched or insufficient pixels in '${name}'.`);
    }
  }
  if (meta.audio && !(await archive.read(meta.audio.file, MAX_AUDIO_BYTES)).length)
    throw new Error("Invalid TSOG: the embedded audio file is empty.");
  if (decodeImage) await decodeTSOG(meta,
    async (name) => decodeImage(await archive.read(name), signal),
    { maxPoints: Infinity, signal },
  );
  return {
    valid: true,
    level: decodeImage ? "attributes" : "container",
    profile: meta.profile ?? null,
    legacy: meta.profile === undefined,
    count: meta.count,
    timeline: !meta.timeline ? "static" : meta.timeline.type === 0 ? "discrete" : "continuous",
    images: names.size,
    audio: Boolean(meta.audio),
    audioCodecChecked: false,
  };
}
