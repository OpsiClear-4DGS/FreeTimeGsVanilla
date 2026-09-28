// Repackage existing attributes without decoding or recompressing their images.
import { openZip, crc32 } from "./zip.js?v=6";
import { validateTSOGMetadata } from "./tsog.js?v=6";
import { audioMimeType, MAX_AUDIO_BYTES } from "./playback.js?v=6";

function storedZip(entries) {
  const body = [],
    directory = [];
  let offset = 0,
    directorySize = 0;
  if (entries.length > 1024) throw new Error("Too many package entries.");
  for (const [name, bytes] of entries) {
    const label = new TextEncoder().encode(name);
    const local = new Uint8Array(30),
      header = new DataView(local.buffer);
    const central = new Uint8Array(46),
      record = new DataView(central.buffer);
    if (
      label.length > 65535 ||
      bytes.length >= 0xffffffff ||
      offset >= 0xffffffff
    )
      throw new Error("The package exceeds ZIP32 limits.");
    const crc = crc32(bytes);
    header.setUint32(0, 0x04034b50, true);
    header.setUint16(4, 20, true);
    header.setUint16(6, 0x800, true); // UTF-8 filenames; stored entries.
    header.setUint32(14, crc, true);
    header.setUint32(18, bytes.length, true);
    header.setUint32(22, bytes.length, true);
    header.setUint16(26, label.length, true);
    record.setUint32(0, 0x02014b50, true);
    record.setUint16(4, 20, true);
    record.setUint16(6, 20, true);
    record.setUint16(8, 0x800, true);
    record.setUint32(16, crc, true);
    record.setUint32(20, bytes.length, true);
    record.setUint32(24, bytes.length, true);
    record.setUint16(28, label.length, true);
    record.setUint32(42, offset, true);
    body.push(local, label, bytes);
    directory.push(central, label);
    offset += local.length + label.length + bytes.length;
    directorySize += central.length + label.length;
  }
  if (offset + directorySize >= 0xffffffff)
    throw new Error("The package exceeds ZIP32 limits.");
  const footer = new Uint8Array(22),
    end = new DataView(footer.buffer);
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, entries.length, true);
  end.setUint16(10, entries.length, true);
  end.setUint32(12, directorySize, true);
  end.setUint32(16, offset, true);
  return new Blob([...body, ...directory, footer], { type: "application/zip" });
}

/** Add/replace audio (File), remove it (null), or update playback defaults. */
export async function packageTSOG(
  input,
  { audio, playback, volume, signal } = {},
) {
  const archive = await openZip(input, signal);
  const meta = validateTSOGMetadata(
    JSON.parse(
      new TextDecoder().decode(
        await archive.read("meta.json", 4 * 1024 * 1024),
      ),
    ),
  );
  const oldAudio = meta.audio?.file;
  if (playback !== undefined) {
    if (!playback || typeof playback !== "object" || Array.isArray(playback))
      throw new TypeError("playback must be an object.");
    meta.playback = { ...meta.playback, ...playback };
  }
  if (audio === null) delete meta.audio;
  else if (audio !== undefined) {
    if (!(audio instanceof Blob) || !audio.size || audio.size > MAX_AUDIO_BYTES)
      throw new TypeError(
        "Audio must be a nonempty File or Blob of at most 128 MiB.",
      );
    const extension = audio.name?.split(".").pop().toLowerCase();
    const mimeType = audioMimeType(audio.name ?? "") || audio.type;
    const file = `audio/track.${audioMimeType(audio.name ?? "") ? extension : "bin"}`;
    if (archive.names.includes(file) && file !== oldAudio)
      throw new Error(`Package already contains '${file}'.`);
    meta.audio = { file, mimeType, volume: volume ?? meta.audio?.volume ?? 1 };
  }
  if (volume !== undefined) {
    if (!meta.audio) throw new Error("A volume default needs an audio track.");
    meta.audio.volume = volume;
  }
  validateTSOGMetadata(meta);
  const entries = [
    [
      "meta.json",
      new TextEncoder().encode(JSON.stringify(meta, null, 2) + "\n"),
    ],
  ];
  for (const name of archive.names) {
    if (name === "meta.json" || (audio !== undefined && name === oldAudio))
      continue;
    entries.push([
      name,
      await archive.read(name, name === oldAudio ? MAX_AUDIO_BYTES : undefined),
    ]);
  }
  if (audio instanceof Blob)
    entries.push([meta.audio.file, new Uint8Array(await audio.arrayBuffer())]);
  signal?.throwIfAborted();
  return storedZip(entries);
}
