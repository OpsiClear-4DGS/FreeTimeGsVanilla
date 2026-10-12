# Loading performance

TSOG playback retains all Gaussians, all SH bands and the original float32
attributes. The optimization changes decoding work and GPU storage; the TSOG
file, reconstruction and audio are unchanged.

## Measured example

Jensen: 2,000,000 Gaussians, degree-3 SH, 65,536 shared centroids, 121
timestamps, embedded audio, 45,106,423-byte TSOG. Measured on 2026-10-12 in
Chromium 153 on Linux, headless SwiftShader, at 640×480. Each value is the
median of three page loads. The baseline is commit `4af9e12`.

| Stage / data | Baseline | Optimized |
| --- | ---: | ---: |
| Decode a resident TSOG Blob | 5.32 s | 0.95 s |
| Upload attributes, including GPU completion | 0.57 s | 0.11 s |
| Decode + upload | 5.91 s | 1.06 s |
| Decoded attribute arrays | 648.0 MB | 183.7 MB |
| Attribute bytes uploaded to GPU | 640.0 MB | 175.7 MB |
| Logical Gaussians / SH coefficients per point | 2M / 16 | 2M / 16 |

Decode plus upload is about 82% shorter in this test. Download time is measured
separately and is excluded from these timings. Network conditions, real GPUs,
mobile browsers, sorting, and audio readiness affect time to interactive
playback. The memory figures count attribute arrays and upload payloads;
they are not process peak memory or total allocated VRAM.

## Changes

- Keep one DC coefficient and centroid label per point, followed by the shared
  higher-band SH palette. The shader resolves the original coefficients from
  that table. Small samples use dense storage if it is smaller.
- Cache signed-log position values for 16-bit codes and scale variances for
  codebook entries. Reuse quaternion and covariance scratch buffers.
- Process rows with a 12 ms time budget, yielding without nested timer clamps.
  Progress and cancellation remain available during long batches.
- Decode at most two attribute images together and complete both before
  releasing the WebGL decoder. Keep byte-exact native WebP decoding and all
  ZIP checksums, size checks, metadata validation and abort checks.

The low-level TSOG reader still returns dense SH arrays by default. The player
selects the compact representation automatically. This is an internal layout
choice, rather than a quality setting.

## Verification and reproduction

All position/time, motion/duration, covariance, opacity and logical SH values
match the baseline byte for byte on the full Jensen example. Rendered frames
at normalized times 0.15 and 0.7 have identical SHA-256 pixel hashes. Synthetic
checks cover SH0–3, continuous/discrete/static timelines, 8/16-bit motion,
point sampling, malformed files, scheduler fallback and cancellation. Browser
checks cover audio, canvas/iframe controls, opaque sandboxes and interrupted
downloads. These are Chromium checks; they do not establish device-specific
performance or compatibility for every browser.

With the optional [test dependencies](../tests/requirements.txt) and Chromium:

```bash
python tools/benchmark-loading.py --output /tmp/loading.json --capture
```

Capture a baseline from the earlier source tree using the same benchmark
script and browser. Then run the new source with:

```bash
python tools/benchmark-loading.py --output /tmp/optimized.json \
  --compare /tmp/loading.json
```

`--compare` checks all logical attributes and rendered frames for exact
equality. Hashing happens after timed stages. `--browser PATH` selects a browser;
`--root PATH` selects another source tree. The benchmark serves files locally
and loads every point. In the bundled FTGS copy the manifest downloads the
same hosted example before starting the decode timer. Run without competing
benchmarks, and retain large JSON results outside the repository.
