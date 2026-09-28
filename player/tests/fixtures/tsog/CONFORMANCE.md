# TSOG Playback Profile 1 conformance corpus

All data here is synthetic. See [the specification](../../../TSOG-SPEC.md) for
normative requirements and [the guide](../../../TSOG.md#original-work-and-licensing)
for source attribution and licenses. No captured scene or recorded audio is used.

## Portable cases

- [metadata-cases.json](metadata-cases.json): start with `base`, deep-copy it for
  each case, and assign each `[dot.separated.path, value]` in `edits`. `valid`
  gives expected semantic acceptance. `schemaValid`, when present, gives the
  less strict structural schema result; otherwise it equals `valid`.
- [decode-vector.json](decode-vector.json): two hand-computable Gaussians, with
  metadata and raw RGBA images. `pixels` lists row-major pixels; `fill` repeats
  one pixel across the declared dimensions. These bytes bypass WebP so any
  implementation can check attribute decoding independently of its image codec.
  Expected arrays use the reference model layout: positionTime is `[x,y,z,center]`,
  velocityDuration `[vx,vy,vz,sigma]`, covarianceA `[Cxx,Cxy,Cxz,Cyy]`, and SH
  `[red,green,blue,unused=0]` per coefficient per Gaussian. Alpha is separate.
  The absolute tolerance is 1e-6. Positions/opacity at normalized time zero and
  resolved playback settings are also provided.

## Encoded packages

| File | Expected attributes |
| --- | --- |
| [continuous8.tsog](continuous8.tsog) | 256 Gaussians; degree 3; continuous center/sigma; 8-bit translation. |
| [continuous16.tsog](continuous16.tsog) | Same synthetic source; 16-bit translation. |
| [discrete.tsog](discrete.tsog) | 256 Gaussians; degree 3; four frames with 64 Gaussians each; legacy FPS 24; duration 1/6 second. |
| [static.tsog](static.tsog) | 256 Gaussians; degree 0; no timeline or motion. |

These are legacy v4 archives produced with the pinned original encoder; their
bytes are kept unchanged to exercise compatibility. [image-hashes.json](image-hashes.json)
records the dimensions and SHA-256 of the exact **decoded RGBA bytes**, including
unused channels and image padding. Hashes must match exactly; lossy source
quantization is a separate comparison, not a reason for hash differences.

[tsog-source.mjs](../../tsog-source.mjs) defines source attributes. Against these
source values, [browser_tsog.py](../../browser_tsog.py) uses absolute tolerances:

| Quantity | Maximum error |
| --- | --- |
| Position | 0.0001 |
| 8-bit / 16-bit velocity | 0.004 / 0.00002 |
| DC / higher SH | 0.02 / 0.003 |
| Covariance component | 0.00003 |
| Opacity | 1/255 |
| Temporal center / sigma | 0.00002 / 0.00001 |

## Versioned packages, audio and negative cases

[conformance.test.mjs](../../conformance.test.mjs) repackages each legacy archive
in memory to add the v1 declaration, validates it, and checks that all original
attribute bytes survive. It covers unknown profile versions, metadata semantics,
lossless image headers, CRC errors, and the CLI's exit codes and validation scope.
[validate-tsog-schema.py](../../validate-tsog-schema.py) applies the independent
Draft 2020-12 validator to the same metadata cases and synthetic archives.

[audio-fixture.mjs](../../audio-fixture.mjs) generates a quiet PCM sine tone in
memory. [playback.test.mjs](../../playback.test.mjs) checks metadata, packaging and
timing. [browser_audio.py](../../browser_audio.py) checks real media playback,
autoplay denial/retry, seek, speed, loop/end, mute/volume, lifecycle and XR pauses.
Tests create versioned audio packages in memory or temporary directories; there
is no recorded track to distribute.

Full browser validation also checks every decoded Gaussian without a point
limit. Passing container checks alone does not establish pixel or codec validity.
Regeneration instructions for the original synthetic packages are in the guide.
