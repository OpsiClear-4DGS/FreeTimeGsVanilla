# Packaged TSOG support

Drop one `.tsog` file into the player, choose it with the file button, or use
`?src=path/to/scene.tsog`. Both embedding APIs accept the same file through
`load()`, as a File, Blob, or HTTP(S) URL. The package is a ZIP containing
`meta.json` at its root and the referenced WebP attribute images. Loose image
directories and standalone `meta.json` files are not player inputs.

The repository maintains its own browser decoder in [tsog.js](tsog.js), with
[zip.js](zip.js) for packaging and [webp.js](webp.js) for byte-exact attribute
images. It uses native browser WebP decoding and WebGL2, without a runtime
package dependency. Existing `.ftgs.ply` loading remains available.

## Original work and licensing

TSOG was introduced by **Shady Gmira, Evangelos Alexiou, Emmanouil Potetsianakis,
and Emmanuel Thomas**, *TSOG: A Format For Temporally And Spatially Ordered
Gaussians*, ICIP 2026, [arXiv:2607.28049](https://arxiv.org/abs/2607.28049).

The original implementation is
[Xiaomi Research / tsog](https://github.com/xiaomi-research/tsog). This decoder
targets its version-4 packaged output at commit
[`a58102d`](https://github.com/xiaomi-research/tsog/tree/a58102d0489a68c4f7e18e5e1ddaf613256417dd).
The reference encoder is distributed under the **Clear BSD License**, copyright
2026 Xiaomi Corporation, as recorded in its
[LICENSE](https://github.com/xiaomi-research/tsog/blob/a58102d0489a68c4f7e18e5e1ddaf613256417dd/LICENSE).
The base attribute conventions originate in
[PlayCanvas SOG](https://developer.playcanvas.com/user-manual/gaussian-splatting/formats/sog/)
and its [MIT-licensed splat-transform](https://github.com/playcanvas/splat-transform).
Full notices are retained in [THIRD_PARTY.md](THIRD_PARTY.md); preserve that file
when redistributing this module. The player's own code remains under its
repository license.

```bibtex
@article{gmira2026tsog,
  title   = {TSOG: A Format For Temporally And Spatially Ordered Gaussians},
  author  = {Gmira, Shady and Alexiou, Evangelos and Potetsianakis, Emmanouil and Thomas, Emmanuel},
  year    = {2026},
  journal = {arXiv preprint arXiv:2607.28049},
  doi     = {10.48550/arXiv.2607.28049}
}
```

## Audio and playback metadata

This player adds two **optional root fields** to `meta.json`. These are local
player extensions, not fields defined by the original TSOG exporter or SOG
specification. The package still uses TSOG version 4, and its Gaussian images
are unchanged. Files without these fields retain the defaults described below.

```json
{
  "playback": { "duration": 10, "fps": 30, "rate": 1, "loop": true },
  "audio": { "file": "audio/track.mp3", "mimeType": "audio/mpeg", "volume": 0.8 }
}
```

Add these fields alongside the existing `version`, `count`, and attributes.

| Field | Meaning / default |
| --- | --- |
| `playback.duration` | Positive clip length in seconds at 1× speed. Optional for animation; required for a static scene with audio. |
| `playback.fps` | Positive display frame rate; falls back to the existing top-level `fps`, then 30. |
| `playback.rate` | Speed multiplier from 0.25 to 4; default 1. |
| `playback.loop` | Whether to repeat the entire clip; default true. |
| `audio.file` | Required when `audio` is present: one file inside the same ZIP. External URLs and traversal paths are rejected. |
| `audio.mimeType` | Audio MIME type; inferred from a known filename extension if omitted. |
| `audio.volume` | Initial volume from 0 to 1; default 1. |

For continuous scenes, duration and FPS establish the display frame count as
`round(duration * fps) + 1` (at least two); duration remains exact. For discrete
scenes, duration implies `fps = N / duration`. If both `playback.duration` and
`playback.fps` are present, they must agree with `N`. Explicit player `fps` or
continuous `frames` overrides recalculate the animation duration using that
frame count. A static scene keeps its declared duration.

Speed changes advance scene time and audio together. `duration` and
`currentTime` always refer to media seconds: a ten-second clip at 2× takes five
seconds to play. Audio begins at scene time zero, follows seek/pause/loop, and
stops at the clip's end. Shorter tracks leave silence at the end; longer tracks
are cut off there. Audio does not loop independently. The player corrects clock
drift; it does not promise sample-accurate synchronization with GPU rendering.

Audio is optional and limited to 128 MiB per track. MP3, M4A/AAC, Ogg/Opus, WAV,
FLAC and WebM are accepted containers; actual codec support depends on the
browser. Unsupported audio leaves the scene playable and reports an audio
error. A missing referenced file, invalid metadata or a failed ZIP checksum
rejects the package. Object URLs are released when replacing or destroying the
player. Hiding the desktop tab or losing XR tracking pauses audio with the scene.

Browsers may block audible autoplay. The scene continues, and the sound button
shows **Enable audio**. Click it (or press M) to retry from the current scene
time. The API exposes `audio.status` and `audiochange` for custom controls. See
[MDN's autoplay guide](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay).

### Add audio or change defaults

The dependency-free packaging command uses Node.js 22 or newer:

```bash
node tools/package-tsog.mjs scene.tsog scene-with-audio.tsog \
  --audio soundtrack.mp3 --duration 10 --fps 30 --speed 1 --loop --volume 0.8
```

Omit `--audio` to retain the current track, or use `--remove-audio` to remove it.
Use `--no-loop` for a clip that stops at its end. Unspecified defaults and other
metadata are retained; the tool copies the attribute images without encoding
them again. Output must end in `.tsog`; existing output files are never
overwritten. Run `--help` for the options. Use a new output filename for changes.

The same operation is available to browser projects and Node.js:

```js
import { packageTSOG } from "./tsog-package.js";
const result = await packageTSOG(sceneBlob, {
  audio: audioFile, // File or a Blob with an audio MIME type; null removes audio
  playback: { duration: 10, fps: 30, rate: 1.5, loop: false },
  volume: 0.8,
  // signal: abortController.signal,
});
// Download result as a .tsog file, or pass the Blob directly to player.load().
```

The built-in controls include playback speed, mute (M), and a volume slider on
desktop. Explicit constructor/query settings and API setters override file
defaults. Setters persist across subsequent loads. Query parameters are
`speed`, `loop=0|1`, `muted=0|1`, and `volume` alongside the existing timing
options. Both canvas and iframe APIs expose `setPlaybackRate`, `setMuted`, and
`setVolume`; see [API.md](API.md).

## Compatibility

Supported version-4 attributes:

| Attribute | Encoding |
| --- | --- |
| Position | Signed logarithmic coordinates, low/high 8-bit image pairs |
| Scale | Codebook of natural-log standard deviations |
| Rotation | Smallest-three quaternion, omitted component in alpha (252–255), wxyz order |
| Color and opacity | SH DC codebook; alpha is already linear opacity |
| Higher-order color | SH degrees 1–3, centroid codebook and 16-bit palette labels |
| Continuous timeline | Temporal center and positive standard deviation, 16 bits each |
| Discrete timeline | Quantized integer frame indices, `N` frames, optional `fps` |
| Motion | First-order translation, 8 or 16 bits per component |
| No timeline | Static model |

For continuous models, opacity is `alpha * exp(-0.5 * ((t - center) / scale)^2)`
and position is `position + velocity * (t - center)`. Temporal scale is a linear
standard deviation, not a log value. This player uses normalized scene time
`[0, 1]`; the original continuous exporter does not include a whole-clip duration.
Without playback metadata, playback defaults to 300 display frames at 30 fps. Set `?frames=120&fps=24`
or the canvas constructor's `frames` and `fps` to choose the playback duration.
Files using a different global time unit need conversion before playback.

For discrete models, only the selected frame is visible. No cross-frame fade or
motion is applied. The package's `N` sets the frame count and `fps` sets the
playback speed (30 if absent). An explicit player `fps` overrides that speed;
`frames` does not override a discrete timeline. Duration is `N / fps`, including
a full final-frame interval. `seek(t)` remains normalized and selects
`min(N - 1, floor(t * N))`; `seek(1)` displays the last frame. Static files stay
paused unless `playback.duration` supplies a timed presentation.

**Timeline numbering follows the repository's encoder.** In its version-4
metadata, type `0` means discrete frames and type `1` means continuous
center/scale. The paper describes a broader numbering scheme. This reader
does not reinterpret type `1` as start/duration, or guess at unsupported types.
Other format versions, higher-order motion and animated scale/rotation/color
are rejected with an error.

ZIP stored entries and DEFLATE entries are supported, with CRC checks and
streamed-entry descriptors. Encrypted, multi-disk and ZIP64 archives are not
supported. Each attribute image must fit the device's maximum WebGL texture
size. The reader decodes one attribute group at a time, but loads the complete
package; it does not stream a scene. The point limit is shared across all
frames and samples uniformly over encoded Gaussian indices. A very low limit
can leave some discrete frames empty. Selected points still need CPU and GPU
memory; the point limit does not reduce source image dimensions.

## Interoperability fixtures

`tests/fixtures/tsog/` contains small synthetic packages, without captured scene
data. The continuous 8-bit/16-bit motion and discrete packages were produced
with the pinned original encoder. The static package reuses those encoded
attributes with timeline and higher-order color omitted. Reference WebP RGBA
hashes are checked against the browser decoder, including low-alpha colors
and quaternion tags. Source attributes are compared within quantization error.

Regenerate only when needed, using a separate reference checkout:

```bash
# In the pinned xiaomi-research/tsog checkout:
npm ci --ignore-scripts
npm run build
# In this player's directory:
node tests/generate-tsog-fixtures.mjs /path/to/tsog
```

The player itself does not install or bundle the reference tool or its packages.
To test the decoder, packaging, temporal selection and browser playback:

```bash
npm test
uv run --no-project --with playwright --with pillow python tests/browser_tsog.py
uv run --no-project --with playwright python tests/browser_audio.py
```

The browser test also accepts `--browser /path/to/chromium`. It checks rendered
continuous and discrete animation, seek boundaries, final-frame timing, point
sampling, cancellation, file drop, URLs, iframe loading and malformed-file
recovery. `tests/browser_xr.py` additionally tests both TSOG timeline modes in a
simulated WebXR session with real WebGL rendering.

The audio tests generate a quiet sine tone in memory using
`tests/audio-fixture.mjs`. They cover packaging, timing defaults, autoplay
denial/retry, speed, seek, looping, mute/volume, mobile controls, both embedding
APIs, replacement/disposal, and audio during simulated XR tracking loss. No
recorded scene or audio is needed or included.
