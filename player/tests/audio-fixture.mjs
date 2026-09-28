// Procedural audio only: a quiet, pulsed sine tone, with no recorded material.
export function toneFile(duration = 4) {
  const sampleRate = 16000,
    samples = Math.round(duration * sampleRate);
  const bytes = new Uint8Array(44 + samples * 2),
    view = new DataView(bytes.buffer);
  const label = (offset, text) =>
    bytes.set(new TextEncoder().encode(text), offset);
  label(0, "RIFF");
  view.setUint32(4, bytes.length - 8, true);
  label(8, "WAVE");
  label(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  label(36, "data");
  view.setUint32(40, samples * 2, true);
  for (let i = 0; i < samples; i++) {
    const t = i / sampleRate,
      pulse = (t % 1) / 0.3;
    const envelope = pulse < 1 ? Math.sin(Math.PI * pulse) ** 2 : 0;
    view.setInt16(
      44 + 2 * i,
      Math.round(3000 * envelope * Math.sin(2 * Math.PI * 440 * t)),
      true,
    );
  }
  return new File([bytes], "synthetic-tone.wav", { type: "audio/wav" });
}
