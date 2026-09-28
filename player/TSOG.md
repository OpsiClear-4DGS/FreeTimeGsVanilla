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
`[0, 1]`; the continuous exporter does not include a whole-clip duration.
Playback therefore defaults to 300 display frames at 30 fps. Set `?frames=120&fps=24`
or the canvas constructor's `frames` and `fps` to choose the playback duration.
Files using a different global time unit need conversion before playback.

For discrete models, only the selected frame is visible. No cross-frame fade or
motion is applied. The package's `N` sets the frame count and `fps` sets the
playback speed (30 if absent). An explicit player `fps` overrides that speed;
`frames` does not override a discrete timeline. Duration is `N / fps`, including
a full final-frame interval. `seek(t)` remains normalized and selects
`min(N - 1, floor(t * N))`; `seek(1)` displays the last frame. Static files stay
paused.

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
```

The browser test also accepts `--browser /path/to/chromium`. It checks rendered
continuous and discrete animation, seek boundaries, final-frame timing, point
sampling, cancellation, file drop, URLs, iframe loading and malformed-file
recovery. `tests/browser_xr.py` additionally tests both TSOG timeline modes in a
simulated WebXR session with real WebGL rendering.
