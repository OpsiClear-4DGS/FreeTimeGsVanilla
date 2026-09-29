# TSOG Playback Profile 1

Specification version **1.0.1**, 2026-09-28. Maintained by **OpsiClear-4DGS**.

This document specifies a `.tsog` interchange profile: the supported subset of
Xiaomi Research's version-4 Gaussian encoding, plus optional playback defaults
and one embedded audio track. It defines the bytes and their interpretation so
that an independent reader can implement them. It does not redefine every
timeline or temporal model discussed in the TSOG paper, and is not an official
Xiaomi or PlayCanvas specification.

**MUST**, **MUST NOT**, **SHOULD**, and **MAY** express requirements, recommendations,
and permitted choices. Sections 1–9 are normative. Section 10 describes the
reference tools and conformance examples. The [packaging guide](TSOG.md) covers
commands and player controls; this document governs file interpretation.

## 1. Identification and compatibility

A new conforming writer MUST emit both identifiers in root `meta.json`:

```json
{
  "version": 4,
  "profile": { "id": "org.opsiclear.tsog-playback", "version": 1 }
}
```

This fragment identifies two independent versions:

| Field | Meaning |
| --- | --- |
| `version: 4` | Xiaomi reference encoder's base attribute encoding. |
| `profile.id` | This repository's packaged playback profile. |
| `profile.version: 1` | Required interpretation of this profile, including playback and audio. |

Readers MUST reject an unsupported base version, profile ID, or profile version.
They MUST NOT silently guess a newer profile's semantics. Readers MUST also
accept supported legacy v4 files without `profile`, using the same defaults and
decoding rules. This includes earlier files with unversioned `playback`/`audio`.
Absence of a marker is legacy compatibility, not a declaration of conformance.
The `--require-profile` validation option disallows that legacy fallback.

Unknown optional members MUST be ignored, including members of `playback`,
`audio`, and `profile`. They MUST NOT change the interpretation of known fields.
The exception is `temporal`: unknown attribute names there MUST be rejected
because ignoring animated geometry or appearance would change the scene.
Metadata-preserving tools SHOULD retain unknown members and unreferenced files.

Additive, ignorable annotations and editorial clarifications may retain profile
version 1. A new required behavior, changed default, new timeline type, or changed
meaning of existing data requires a new profile version. A changed base encoding
also requires its applicable base version. Existing version identifiers MUST NOT
be reassigned to incompatible meanings. Future profiles need a separately
published specification and schema; this v1 schema's meaning is fixed.

## 2. Container and references

A `.tsog` is a ZIP archive containing exactly one root `meta.json`, its referenced
attribute images, and optionally the referenced audio file. A `.sog` file is not
interchangeable merely because it also uses ZIP and WebP. Transport MAY use
`application/zip`; this profile does not register a new media type.

```text
scene.tsog
├── meta.json
├── means_l.webp
├── means_u.webp
├── scales.webp
├── quats.webp
├── sh0.webp
├── ...other referenced attribute images
└── audio/track.mp3                 (optional)
```

ZIP methods 0 (STORE) and 8 (DEFLATE) are supported. Readers MUST support valid
data descriptors, use central-directory sizes/offsets, and check uncompressed
size and CRC-32 for every consumed entry. Encryption, ZIP64, split/multi-disk
archives, duplicate entry names, unsupported methods, inconsistent local
headers, and truncated records MUST be rejected. Entry order is insignificant.
Unreferenced entries MAY be ignored and MUST NOT be executed.

For consumed entries without a data descriptor, the local CRC and compressed/
uncompressed sizes MUST equal the central-directory values. With the descriptor
flag set, these local fields are zero and a trailing descriptor supplies the
matching CRC and sizes. Readers MUST accept descriptors both with and without
their optional signature, and reject missing, truncated or conflicting records.
The container follows the [PKWARE ZIP specification](https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT).

Referenced names are literal, case-sensitive UTF-8 ZIP names, relative to the
archive root. They MUST be nonempty and MUST NOT contain backslashes, U+0000–001F,
colon, `?`, `#`, an empty path segment, or a `.`/`..` segment. There is no URL
decoding, Unicode normalization, filesystem lookup, or external network fetch.
Attribute references MUST NOT name `meta.json`. Names in each `files` array MUST
be distinct; their order is significant. Sharing a file across groups is legal
only if all its interpretations satisfy this specification. `audio.file` MUST
be distinct from metadata and all attribute references. Writers SHOULD use
portable ASCII names and MUST set the ZIP UTF-8 flag for non-ASCII names.

Profile limits are 1,024 ZIP entries, a 4 MiB central directory, a 4 MiB
uncompressed `meta.json`, 512 MiB per uncompressed attribute entry, and 128 MiB
for the audio entry. ZIP32 size/offset limits also apply. MiB means 1,048,576
bytes. Readers MAY have lower device/memory limits but MUST report a resource
limitation rather than claim a file is structurally invalid for that reason.

## 3. Metadata model

`meta.json` MUST be a UTF-8 JSON object. Writers MUST use unique member names;
readers MAY reject duplicates, or use the last occurrence. `null` is not an
omitted value. Numeric fields MUST be finite numbers; integers MUST be exact.
Codebook entries and range endpoints MUST lie within the finite float32 range
`[-3.4028234663852886e38, 3.4028234663852886e38]`. Decoded attributes and covariance
components MUST also remain finite when rounded to float32.

The [JSON Schema](tsog.schema.json) uses Draft 2020-12. It checks field structure
and scalar limits, including legacy imports. Cross-field relationships, archive
contents, numeric decoding, and timing are additional requirements below.

| Member | Type and requirement |
| --- | --- |
| `version` | Required integer, exactly 4. |
| `profile` | Object with the ID/version in §1; required for new conforming writers. |
| `count` | Required integer in `[1, 9007199254740991]`; number of valid Gaussian pixels. |
| `means` | Required object: `mins`, `maxs` are 3-number arrays; `files` contains two names, low byte then high byte. |
| `scales` | Required object: `codebook` and one-element `files`. |
| `quats` | Required object: one-element `files`. |
| `sh0` | Required object: `codebook` and one-element `files`. |
| `shN` | Optional object: integer `bands` 1–3, integer palette `count` 1–65536, `codebook`, and two `files`: centroids then labels. |
| `timeline` | Optional discriminated object, specified in §6. Absence means static. |
| `temporal` | Optional object containing only `means`, as specified in §7; requires `timeline`. |
| `fps` | Optional positive number: legacy frame-rate default. |
| `playback` | Optional object described in §8. |
| `audio` | Optional object described in §9. |

A `codebook` is an array of 1–256 numbers. Every used image index MUST be less
than its codebook length. Short books are legal; indices are not rescaled.
For every range pair, `mins[i] <= maxs[i]`. Equal endpoints describe a constant
attribute and are legal. No sorting of codebooks or Gaussians is implied.

A complete static metadata example follows. Each image has at least one pixel;
all used RGB codebook indices are zero. An example quaternion pixel is
`[128, 128, 128, 252]`. This metadata does not itself contain the image files.

```json
{
  "version": 4,
  "profile": { "id": "org.opsiclear.tsog-playback", "version": 1 },
  "count": 1,
  "means": {
    "mins": [0, 0, 0], "maxs": [0, 0, 0],
    "files": ["means_l.webp", "means_u.webp"]
  },
  "scales": { "codebook": [-2.302585092994046], "files": ["scales.webp"] },
  "quats": { "files": ["quats.webp"] },
  "sh0": { "codebook": [0], "files": ["sh0.webp"] }
}
```

## 4. Images, indexing and coordinates

Attribute images MUST be still, lossless WebP (`VP8L`); optional `VP8X` wrappers
must describe the same dimensions. Lossy `VP8`, separate `ALPH`, and animated
WebP are not attribute encodings in this profile. Decoders MUST recover raw
8-bit R, G, B, A values without gamma/color-profile conversion, premultiplication,
or orientation changes. If WebP has no alpha, A is 255. Color values at low or
zero alpha must survive unchanged; ordinary premultiplied canvas extraction is
not sufficient.

All per-Gaussian images, including higher-SH labels and temporal images, MUST
share width `W` and height `H`, with `W*H >= count`. Dimensions are positive and
bounded by WebP's 16,384 per axis. Gaussian `i` uses pixel
`(i % W, floor(i / W))`, counted from the top-left in row-major order.
Readers MUST ignore trailing padding pixels. The SH centroid image has its own
dimensions (§5.4). Unmentioned channels are ignored.

Positions, rotations, scales, velocities and SH directions use a consistent
right-handed scene space. The conventional default view uses X right, Y up,
Z back; a file contains neither a preferred camera nor a physical unit scale.
Reorienting a scene for display must transform its geometry and SH directions
consistently. AR placement scale and camera controls are presentation choices.
Spatial scale values are principal-axis Gaussian standard deviations in scene
units. Continuous time is normalized as defined in §6; audio uses media seconds.

In formulas below, `L.c`/`H.c` are the low/high image's channel bytes, and
`lerp(a,b,t) = a + (b-a)*t`. Two-byte values use `L.c + 256*H.c`.

## 5. Spatial attributes

### 5.1 Position

For axis `a = 0,1,2`, use R,G,B respectively from `means.files`:

```text
q = low[a] + 256 * high[a]
n = lerp(means.mins[a], means.maxs[a], q / 65535)
position[a] = sign(n) * (exp(abs(n)) - 1)
```

`sign(0) = 0`. Ranges are in the signed-log domain. An implementation SHOULD use
`expm1(abs(n))` for accuracy near zero. Alpha is ignored.

### 5.2 Scale, rotation and covariance

R,G,B of the scale image index the shared log-scale codebook:
`s[a] = exp(scales.codebook[pixel[a]])`.

Quaternion components are ordered **w,x,y,z**. A quaternion pixel's alpha MUST
be one of 252,253,254,255; `omitted = A-252` identifies the missing component.
Decode the three kept components, in wxyz order with that component skipped:

```text
kept[c] = (2 * pixel[c] / 255 - 1) / sqrt(2)    # c = R,G,B
missing = sqrt(max(0, 1 - sum(kept[c]^2)))
```

Insert `missing` at `omitted`, then normalize the resulting four-vector before
forming a rotation. The omitted component is nonnegative. For normalized
`q = [w,x,y,z]`, the row-major rotation matrix is:

```text
R = [1-2(y*y+z*z),  2(x*y-w*z),    2(x*z+w*y)
     2(x*y+w*z),    1-2(x*x+z*z),  2(y*z-w*x)
     2(x*z-w*y),    2(y*z+w*x),    1-2(x*x+y*y)]
covariance = R * diag(s[0]^2, s[1]^2, s[2]^2) * transpose(R)
```

### 5.3 Base color and opacity

R,G,B in `sh0.files[0]` are codebook indices for the three DC SH coefficients.
Base opacity is **already linear**: `alpha = A/255`. Do not apply a sigmoid.
Without higher SH, RGB is `max(0, 0.5 + 0.28209479177387814 * DC)` per channel.
Color coefficients describe gamma-space color; attribute bytes themselves are
integer codes and MUST NOT undergo color conversion.

### 5.4 Higher spherical harmonics

`shN.bands` is the maximum SH degree. The number of additional coefficients per
color channel is `K = (bands+1)^2 - 1`: 3, 8, or 15. The centroid image MUST have
width `64*K` and height `ceil(shN.count/64)`. These widths are **192, 512, 960**.

For a Gaussian, its labels image gives `label = R + 256*G`, which MUST be below
`shN.count`. For coefficient `c` in `[0,K)`, read centroid pixel
`x=(label % 64)*K+c`, `y=floor(label/64)`. Its R,G,B index `shN.codebook` for that
coefficient's red, green and blue values. Labels B/A and centroids A are ignored.
DC is coefficient 0; these additional values are coefficients 1 through K.

The real SH basis below fixes the coefficient order and signs. Let `(x,y,z)` be
the unit direction **from the camera to the current Gaussian center**, expressed
in scene space. At a zero-length direction, readers MAY use `(0,0,0)`.

| Index | Basis value |
| --- | --- |
| 0 | `0.28209479177387814` |
| 1 | `-0.4886025119029199*y` |
| 2 | `0.4886025119029199*z` |
| 3 | `-0.4886025119029199*x` |
| 4 | `1.0925484305920792*x*y` |
| 5 | `-1.0925484305920792*y*z` |
| 6 | `0.31539156525252005*(2*z*z-x*x-y*y)` |
| 7 | `-1.0925484305920792*x*z` |
| 8 | `0.5462742152960396*(x*x-y*y)` |
| 9 | `-0.5900435899266435*y*(3*x*x-y*y)` |
| 10 | `2.890611442640554*x*y*z` |
| 11 | `-0.4570457994644658*y*(4*z*z-x*x-y*y)` |
| 12 | `0.3731763325901154*z*(2*z*z-3*x*x-3*y*y)` |
| 13 | `-0.4570457994644658*x*(4*z*z-x*x-y*y)` |
| 14 | `1.445305721320277*z*(x*x-y*y)` |
| 15 | `-0.5900435899266435*x*(x*x-3*y*y)` |

Evaluate RGB as `max(0, 0.5 + sum(basis[j] * coefficient[j]))`, independently
per channel through the stored degree. There is no encoded exposure or color
correction. Rasterization filters, antialiasing, tone mapping, and output display
conversion are outside this interchange specification.

## 6. Timeline

The type numbers below follow the **pinned v4 encoder**, not every numbering
scheme in the research paper. Other types MUST be rejected.

### 6.1 Static

Absent `timeline` means all Gaussians remain static and retain their base
opacity. `temporal` MUST be absent. A static scene can have a timed presentation
through `playback.duration`, including audio, without gaining geometric motion.

### 6.2 Discrete (`type: 0`)

Required metadata: integer `N` in `[1,65536]`, integer
`delta = floor(65536/N)`, and two `files` (low then high). Decode each Gaussian's
integer frame from red only:

```text
q = L.R + 256 * H.R
frame = min(N-1, floor(q / delta))
```

The clamp is intentional, including codes above the final nominal bucket.
Writers SHOULD use `frame*delta + floor(delta/2)` to encode a frame. There are
N frames numbered 0 through N-1. A Gaussian is visible only in its assigned
frame, with its base opacity. There is no interpolation, temporal fade, or motion
between frames. A retained `temporal.means` group is validated but has no effect.

### 6.3 Continuous (`type: 1`)

Required metadata: `mins`/`maxs`, each a two-number array, and two `files` (low
then high). The first range describes temporal center; the second describes a
**linear positive standard deviation**, not a logarithm, duration, or variance.
The minimum deviation MUST remain positive when rounded to float32.

```text
center = lerp(mins[0], maxs[0], (L.R + 256*H.R) / 65535)
sigma  = lerp(mins[1], maxs[1], (L.G + 256*H.G) / 65535)
dt = t - center
opacity(t) = baseAlpha * exp(-0.5 * (dt/sigma)^2)
```

Normalized clip time `t` runs from 0 through 1. Centers MAY be outside that range;
their Gaussian tails can still contribute. Values are not clamped. Files using
other time units need conversion of center, sigma and velocity before declaring
this profile. This encoding has no hard start/end window per Gaussian.

## 7. Translation motion

`temporal.means` has `mins` and `maxs` of shape **[1][3]**, plus a `files` array
of length one (8-bit) or two (16-bit low/high). Only first-order translation is
supported. Animated scale, rotation or color, higher polynomial orders, or other
`temporal` keys MUST be rejected.

Use R,G,B for velocity x,y,z. For each axis:

```text
q = one image ? L[a] : L[a] + 256*H[a]
velocity[a] = lerp(mins[0][a], maxs[0][a], q / (one image ? 255 : 65535))
position(t) = position + velocity * (t - center)
```

Velocity is in scene units per unit of normalized clip time. The encoded base
position is the position at that Gaussian's center time, not necessarily t=0.
Motion is zero when absent, and is applied only for continuous timelines.

## 8. Playback metadata and media clock

All members of optional `playback` are optional:

| Member | Type, unit and default |
| --- | --- |
| `duration` | Positive finite seconds of media time at 1×; derived below when absent. |
| `fps` | Positive finite frames/second; falls back to root `fps`, then 30. |
| `rate` | Finite number in `[0.25,4]`; default 1. |
| `loop` | Boolean; default true. |

Let `F = playback.fps ?? fps ?? 30`, with `??` meaning absent-field fallback.
At file defaults, derive the presentation as follows:

| Timeline | Display frames | Media duration D |
| --- | --- | --- |
| Continuous, duration present | `M=max(2,floor(duration*F+0.5)+1)` | Exact declared duration. |
| Continuous, no duration | `M=300` | `(M-1)/F`. |
| Discrete | `M=timeline.N` | Declared duration, otherwise `N/F`. |
| Static | One geometry frame | Declared duration, otherwise untimed. |

Derived frame counts MUST be positive safe integers; derived duration/FPS MUST
be finite and positive. In a discrete scene, a declared duration sets the
effective FPS to `N/duration`. If both `playback.duration` and `playback.fps` are
present, `abs(duration*fps-N) <= 1e-6*N` MUST hold. Root `fps` is only a fallback
and does not impose this agreement. Continuous FPS is a display-sampling hint;
continuous motion may be evaluated at any normalized time.

For timed content at media time `s` in `[0,D]`, `t=clamp(s/D,0,1)`.
Continuous scenes use t; discrete scenes select `min(N-1,floor(t*N))`. Every
discrete frame, including the last, owns a full `D/N` interval. Seeking to D
selects the last frame. A continuous display frame i corresponds to `i/(M-1)`.
Untimed static content has no advancing clock.

While playing, advance s by elapsed wall seconds times `rate`. Rate does not
change D, frame boundaries, or the meaning of a media second. At the end, looping
wraps the scene and audio clock together; otherwise pause at D. A ten-second clip
at rate 2 therefore takes five wall seconds, and still reports duration 10.
Autoplay, camera choice and visibility/tracking pauses are host policies.

Explicit host controls MAY override defaults without changing the file. The
reference player's `speed`/`playbackRate` changes only the multiplier. Its `fps`
and continuous `frames` overrides deliberately retime a clip: after choosing
the frame count, D becomes `(M-1)/fps` for continuous or `N/fps` for discrete.
An explicit FPS alone retains the frame count derived from file defaults.
Static duration is retained. Hosts SHOULD expose speed for synchronized speed
changes and clearly distinguish it from retiming. Runtime setters take precedence
over constructor defaults, which take precedence over file defaults.

## 9. Optional audio

`audio`, when present, MUST be an object with:

| Member | Requirement |
| --- | --- |
| `file` | Required package-relative filename (§2), nonempty audio entry of at most 128 MiB. |
| `mimeType` | Optional string matching `audio/` followed by one or more ASCII letters, digits, `.`, `+`, or `-`, case-insensitively. Parameters are not permitted. |
| `volume` | Optional finite initial gain in `[0,1]`; default 1. |

When MIME type is absent, readers MUST infer it case-insensitively from the final
extension using this table. An unknown extension requires an explicit MIME type.

| Extension | MIME type |
| --- | --- |
| `mp3` | `audio/mpeg` |
| `m4a` | `audio/mp4` |
| `aac` | `audio/aac` |
| `ogg`, `oga`, `opus` | `audio/ogg` |
| `wav` | `audio/wav` |
| `flac` | `audio/flac` |
| `webm` | `audio/webm` |

Only one track is defined. It starts at media time zero, follows seek/pause/rate,
and repeats only with the scene. A shorter track leaves silence until the clip
ends; a longer track stops at D. Audio length MUST NOT redefine clip duration.
A static scene with audio MUST declare positive `playback.duration`. There is
no encoded audio offset, independent loop, mute default, or multi-track mixing.

Missing/empty audio entries, invalid metadata and a failed consumed-entry CRC
make the package invalid. Codec support and permission to play audible sound
are host capabilities: a reader MAY play the scene silently if audio cannot be
decoded or playback is blocked, but MUST report that condition to the host/user.
Exact device latency, resampling/pitch behavior and sample-accurate rendering
synchronization are outside this profile. Embedded audio bytes are preserved;
the packager does not transcode them.

## 10. Validation and conformance examples

The repository provides three complementary checks:

| Check | Scope |
| --- | --- |
| [tsog.schema.json](tsog.schema.json) | Structural metadata validation with any Draft 2020-12 validator. It intentionally accepts legacy files without a profile marker. |
| [tools/validate-tsog.mjs](tools/validate-tsog.mjs) | Metadata semantics, derived timing, required archive entries, their checksums, still/lossless image headers and dimensions, and audio presence/size. |
| [tsog-validate.js](tsog-validate.js) with `decodeImage` | Adds decoding of every Gaussian (no point sampling): quaternion tags, codebook/palette indices and finite decoded attributes. Audio codecs remain unchecked. |

The container CLI needs Node.js 22+. Run from the player directory:

```bash
node tools/validate-tsog.mjs scene.tsog --require-profile
node tools/validate-tsog.mjs scene.tsog --json
```

Exit 0 means the selected checks pass; exit 1 means failure. JSON output includes
`level: "container"` or `"attributes"`, `legacy`, and `audioCodecChecked: false`.
Passing a schema or container check alone does **not** certify compressed image
pixels or audio codec support. Extra unreferenced entries are not decoded.
A profile marker is a writer's declaration, not a checksum or certificate.

For complete attribute validation in a browser with WebGL2:

```js
import { validateTSOGPackage } from "./tsog-validate.js";
import { AttributeImages } from "./webp.js";
const images = new AttributeImages();
try {
  const report = await validateTSOGPackage(sceneBlob, {
    requireProfile: true,
    decodeImage: (bytes, signal) => images.decode(bytes, signal),
  });
  console.log(report);
} finally {
  images.destroy();
}
```

The [conformance corpus](tests/fixtures/tsog/CONFORMANCE.md) uses synthetic data
only. It includes hand-computable decoding vectors and metadata acceptance
cases, original-encoder packages with exact decoded-image hashes, and generated
audio/timing cases. It distinguishes quantization error against source values
from floating-point tolerance against decoded expected values. Test commands:

```bash
npm test
python tests/validate-tsog-schema.py
uv run --no-project --with playwright --with pillow python tests/browser_tsog.py
uv run --no-project --with playwright python tests/browser_audio.py
```

The schema check needs Python's `jsonschema` package (or run
`uv run --no-project --with jsonschema python tests/validate-tsog-schema.py`).
Browser tests accept `--browser /path/to/chromium`. The specification does not
require a byte-identical rendered screenshot: camera, rasterization and device
effects are outside its scope.

## References and attribution

The base encoding is attributed to **Xiaomi Research**, Shady Gmira, Evangelos
Alexiou, Emmanouil Potetsianakis, and Emmanuel Thomas, *TSOG: A Format For
Temporally And Spatially Ordered Gaussians*, ICIP 2026,
[arXiv:2607.28049](https://arxiv.org/abs/2607.28049). The source used to fix v4
interpretation is the [original repository at a58102d](https://github.com/xiaomi-research/tsog/tree/a58102d0489a68c4f7e18e5e1ddaf613256417dd),
under Xiaomi's Clear BSD License. Playback/audio profile additions are maintained
by this repository and do not imply upstream endorsement.

The spatial conventions build on [PlayCanvas's SOG specification](https://developer.playcanvas.com/user-manual/gaussian-splatting/formats/sog/)
and MIT-licensed [splat-transform](https://github.com/playcanvas/splat-transform).
WebP is defined by Google's [lossless bitstream specification](https://developers.google.com/speed/webp/docs/webp_lossless_bitstream_specification).
The schema dialect is [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12).
The specification and repository-owned code use the repository license; full
upstream license notices remain in [THIRD_PARTY.md](THIRD_PARTY.md).

## Revision history

- **1.0.1 (2026-09-28):** Clarifies ZIP integrity checks and expands negative
  conformance cases for archive records and audio MIME inference. Profile version
  1, the schema, attribute encoding and playback semantics are unchanged.
- **1.0 (2026-09-28):** First explicit playback profile. Preserves v4 attribute
  bytes and existing playback/audio meanings; adds profile identification,
  normative decoding/compatibility rules, schema and conformance tools.
