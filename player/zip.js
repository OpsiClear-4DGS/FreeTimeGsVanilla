// ZIP bundle reader: central-directory offsets also handle streamed data descriptors.
const crcTable = Uint32Array.from({ length: 256 }, (_, byte) => {
  for (let bit = 0; bit < 8; bit++)
    byte = (byte >>> 1) ^ (byte & 1 ? 0xedb88320 : 0);
  return byte >>> 0;
});
export function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255];
  return (crc ^ 0xffffffff) >>> 0;
}
const fail = (message) => { throw new Error(`Invalid TSOG ZIP: ${message}`); };

export async function openZip(blob, signal) {
  const read = async (offset, size) => {
    signal?.throwIfAborted();
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(size) || offset < 0 || size < 0 || offset + size > blob.size)
      fail("truncated entry or directory.");
    const bytes = new Uint8Array(await blob.slice(offset, offset + size).arrayBuffer());
    signal?.throwIfAborted();
    return bytes;
  };
  const tail = await read(Math.max(0, blob.size - 65557), Math.min(blob.size, 65557));
  const end = new DataView(tail.buffer);
  let footer = -1;
  for (let i = tail.length - 22; i >= 0; i--)
    if (end.getUint32(i, true) === 0x06054b50 && i + 22 + end.getUint16(i + 20, true) === tail.length) {
      footer = i;
      break;
    }
  if (footer < 0) fail("missing end-of-directory record.");
  if (end.getUint16(footer + 4, true) || end.getUint16(footer + 6, true)) fail("multi-disk archives are unsupported.");
  const count = end.getUint16(footer + 10, true);
  const size = end.getUint32(footer + 12, true);
  const offset = end.getUint32(footer + 16, true);
  if (count === 65535 || size === 0xffffffff || offset === 0xffffffff) fail("ZIP64 archives are unsupported.");
  if (count !== end.getUint16(footer + 8, true) || count > 1024 || size > 4 * 1024 * 1024 || offset + size > blob.size - tail.length + footer)
    fail("invalid central directory.");
  const directory = await read(offset, size);
  const view = new DataView(directory.buffer);
  const entries = new Map();
  let cursor = 0;
  for (let i = 0; i < count; i++) {
    if (cursor + 46 > size || view.getUint32(cursor, true) !== 0x02014b50) fail("invalid directory entry.");
    const flags = view.getUint16(cursor + 8, true);
    const method = view.getUint16(cursor + 10, true);
    const crc = view.getUint32(cursor + 16, true);
    const packed = view.getUint32(cursor + 20, true);
    const unpacked = view.getUint32(cursor + 24, true);
    const nameSize = view.getUint16(cursor + 28, true);
    const recordSize = 46 + nameSize + view.getUint16(cursor + 30, true) + view.getUint16(cursor + 32, true);
    const local = view.getUint32(cursor + 42, true);
    if (cursor + recordSize > size || local + 30 > offset) fail("truncated directory entry.");
    const name = new TextDecoder("utf-8", { fatal: true }).decode(directory.subarray(cursor + 46, cursor + 46 + nameSize));
    if (entries.has(name)) fail(`duplicate entry '${name}'.`);
    if (flags & 1 || ![0, 8].includes(method)) fail("encrypted or unsupported compression method.");
    if (packed === 0xffffffff || unpacked === 0xffffffff || local === 0xffffffff || view.getUint16(cursor + 34, true))
      fail("ZIP64 or multi-disk entries are unsupported.");
    entries.set(name, { flags, method, crc, packed, unpacked, local });
    cursor += recordSize;
  }
  if (cursor !== size) fail("unexpected central-directory data.");

  return {
    async read(name, maxBytes = 512 * 1024 * 1024) {
      const entry = entries.get(name);
      if (!entry) fail(`missing '${name}'.`);
      if (entry.unpacked > maxBytes) fail(`'${name}' exceeds the supported entry size.`);
      const header = new DataView((await read(entry.local, 30)).buffer);
      if (header.getUint32(0, true) !== 0x04034b50 || header.getUint16(8, true) !== entry.method || header.getUint16(6, true) !== entry.flags)
        fail(`invalid local header for '${name}'.`);
      const nameSize = header.getUint16(26, true);
      const localName = new TextDecoder().decode(await read(entry.local + 30, nameSize));
      if (localName !== name) fail(`mismatched local filename for '${name}'.`);
      const dataOffset = entry.local + 30 + nameSize + header.getUint16(28, true);
      if (dataOffset + entry.packed > offset) fail(`truncated '${name}'.`);
      let bytes;
      if (entry.method === 0) {
        if (entry.packed !== entry.unpacked) fail(`invalid stored size for '${name}'.`);
        bytes = await read(dataOffset, entry.packed);
      } else {
        signal?.throwIfAborted();
        const stream = blob.slice(dataOffset, dataOffset + entry.packed).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        const reader = stream.getReader();
        const cancel = () => { void reader.cancel(signal.reason).catch(() => {}); };
        signal?.addEventListener("abort", cancel, { once: true });
        bytes = new Uint8Array(entry.unpacked);
        let written = 0;
        try {
          while (true) {
            signal?.throwIfAborted();
            const { value, done } = await reader.read();
            if (done) break;
            if (written + value.length > bytes.length) fail(`invalid expanded size for '${name}'.`);
            bytes.set(value, written);
            written += value.length;
          }
          signal?.throwIfAborted();
          if (written !== bytes.length) fail(`truncated compressed '${name}'.`);
        } finally {
          signal?.removeEventListener("abort", cancel);
          await reader.cancel().catch(() => {});
          reader.releaseLock();
        }
      }
      signal?.throwIfAborted();
      if (crc32(bytes) !== entry.crc) fail(`checksum failed for '${name}'.`);
      return bytes;
    },
  };
}
