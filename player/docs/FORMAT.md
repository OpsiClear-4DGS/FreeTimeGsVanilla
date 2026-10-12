# FTGS PLY, version 1

`.ftgs.ply` stores the rendering parameters of a Vanilla FreeTimeGS model in a
single PLY file. It describes animated anisotropic Gaussians with normalized
time, temporal opacity, spherical harmonics (SH) and optional linear velocity.
It does not contain optimizer state, capture cameras or frame rate.

## Container

Use PLY `binary_little_endian 1.0`, with one `vertex` element and one row per
Gaussian. Every property is a little-endian IEEE 754 float32. The header is
ASCII, ends with `end_header` and a newline, and must fit in 1 MiB for this player.
The vertex count must be positive. No list properties or other elements are used.

Properties are read by name; their order may vary. The conventional export order
is the table below. All rendering values must be finite, and quaternions must
have nonzero length.

| Properties | Values |
| --- | --- |
| `x`, `y`, `z` | Canonical position at the Gaussian's `time`, in model coordinates |
| `nx`, `ny`, `nz` | Zeros for compatibility with common 3DGS PLY layouts; ignored by this player |
| `f_dc_0`, `f_dc_1`, `f_dc_2` | RGB DC spherical harmonic coefficients |
| `f_rest_0` … | Higher SH coefficients, all red coefficients, then green, then blue |
| `opacity` | Base opacity logit |
| `scale_0`, `scale_1`, `scale_2` | Natural logarithms of spatial standard deviations |
| `rot_0`, `rot_1`, `rot_2`, `rot_3` | Quaternion in **wxyz** order; normalize before use |
| `time` | Canonical time on the normalized sequence timeline |
| `log_duration` | Natural logarithm of the temporal standard deviation |
| `velocity_0`, `velocity_1`, `velocity_2` | Displacement per unit of normalized time |

For SH degree `d`, there are `(d + 1)^2` coefficients per color channel. Degree 0
has only the three DC properties and no `f_rest` properties. Degrees 1, 2 and 3
have 9, 24 and 45 `f_rest` properties respectively. For channel `c` (0=R, 1=G,
2=B) and higher coefficient `k` (1 through `(d + 1)^2 - 1`), its property is
`f_rest_{c * ((d + 1)^2 - 1) + k - 1}`. The real SH basis and ordering match the
usual 3D Gaussian Splatting convention; see [renderer.js](../src/renderer.js).

Velocity properties remain present even when velocity is disabled. Exporters
write the normal fields as zero; this player also accepts files without them.

## Header metadata

Write each setting on its own line as `comment KEY VALUE`:

| Key | Value |
| --- | --- |
| `ftgs_version` | `1` |
| `time_units` | `normalized` |
| `sh_degree` | Integer 0–3 |
| `use_velocity` | `0` or `1` |
| `min_duration` | Minimum temporal standard deviation; Vanilla uses `0.02` |
| `opacity_floor` | Lower bound for temporal opacity; Vanilla uses `0.0001` |
| `n_frames` | Optional positive integer frame count |

All keys except `n_frames` are required. `min_duration` must be finite and
positive; `opacity_floor` must be in `[0, 1)`. Vanilla exporters project
`log_duration` to at least `log(min_duration)` before writing. The player also
applies this lower bound when reading.

For example, a degree-0 file containing one Gaussian begins with this header,
followed immediately by 22 binary float32 values (88 bytes):

```text
ply
format binary_little_endian 1.0
comment ftgs_version 1
comment time_units normalized
comment sh_degree 0
comment use_velocity 1
comment min_duration 0.02
comment opacity_floor 0.0001
comment n_frames 60
element vertex 1
property float x
property float y
property float z
property float nx
property float ny
property float nz
property float f_dc_0
property float f_dc_1
property float f_dc_2
property float opacity
property float scale_0
property float scale_1
property float scale_2
property float rot_0
property float rot_1
property float rot_2
property float rot_3
property float time
property float log_duration
property float velocity_0
property float velocity_1
property float velocity_2
end_header
```

## Evaluation

The sequence spans `t = 0..1`; an individual Gaussian's canonical `time` may lie
outside that interval. Frame `i` maps to `i / max(n_frames - 1, 1)`.

```text
dt = t - time
duration = max(exp(log_duration), min_duration)
position = [x, y, z]
if use_velocity:
    position += [velocity_0, velocity_1, velocity_2] * dt
alpha = max(sigmoid(opacity) * exp(-0.5 * (dt / duration)^2), opacity_floor)
scales = exp([scale_0, scale_1, scale_2])
covariance = R * diag(scales^2) * transpose(R)
```

`R` is the rotation matrix of the normalized wxyz quaternion. Color is
`max(0, 0.5 + SH(direction))`, using the unit direction from the camera to the
animated position and the stored SH coefficients. Degree 0 reduces to
`max(0, 0.5 + 0.28209479177387814 * [f_dc_0, f_dc_1, f_dc_2])`.

Frame rate is a playback choice: this player defaults to 30 fps and accepts
`?fps=24`, `30` or `60`. If `n_frames` is absent, it defaults to 300 frames;
`?frames=120` overrides the frame count.

The speed selector, `?speed=1.5`, or `setPlaybackRate(1.5)` can change playback
speed while leaving this format's stored values unchanged. Audio and persistent
duration/FPS/speed/loop defaults belong to the optional extensions in
[packaged TSOG](TSOG.md#audio-and-playback-metadata); FTGS PLY version 1 has no
audio payload or playback-default fields. See [API.md](API.md) for player controls.

This layout stores the Vanilla FTGS rendering parameters described above.
Changing only a static PLY's filename does not turn it into an FTGS file. The
generated [demo](../site/demo.js) provides a complete writer example without training
dependencies.
