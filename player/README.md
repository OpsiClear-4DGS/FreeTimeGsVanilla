# FTGS Player

A standalone browser player for Vanilla `.ftgs.ply` files. Rendering uses native
WebGL2, with depth sorting in a Web Worker. It runs directly as a static site
without a build step or runtime package installation.

From the repository root:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory player
```

Open <http://localhost:8765> and drop a `.ftgs.ply` anywhere, or click the empty
player to choose a file. Playback starts automatically and loops. The scene fills
the window; a small playback bar fades out when idle and returns on movement or
touch. There are no headers, statistics panels or settings dashboards. The folder
button opens another file. Local files stay in the browser.

Serve the page over HTTP; opening `index.html` directly cannot load its modules
and worker. The synthetic test scene is available explicitly at `/?demo=1`.

Export a trained checkpoint first if needed:

```bash
python src/export_ftgs_ply.py --ckpt path/to/checkpoint.pt --output scene.ftgs.ply
```

To load an HTTP(S) model URL, link with `?src=` followed by its URL-encoded address.
The model host must allow cross-origin requests when it is on a different origin.
The page can be hosted as static files; it has no application server, analytics,
or remote assets.

For a captured scene, a camera inside the capture area can give a much better
initial view than fitting all points. A model link can include `fps=24`, `30` or
`60`, and a URL-encoded JSON `view` parameter, for example:

```js
const query = new URLSearchParams({
  src: "results/scene.ftgs.ply",
  fps: "60",
  view: JSON.stringify({ eye: [0, -1, 0.3], target: [0, 0, 0], up: "z", fov: 45 }),
});
// Link to `/?${query}`. Eye and target use the model's coordinate system.
```

`up` is `y` or `z`; `fov` is the vertical field of view in degrees (10–120).
R restores this bookmarked view for its model. These are player settings;
the FTGS file itself does not store capture cameras or frame rate.

## Controls

| Action | Control |
| --- | --- |
| Play / pause | Play button or Space |
| Seek / step | Timeline, or Left / Right for one frame |
| Open another file | Folder button, O, or drop anywhere |
| Orbit | Left drag / one-finger drag |
| Pan | Right drag, Shift + drag, or two-finger drag |
| Zoom | Scroll / pinch |
| Fit camera / restore saved view | R |
| Switch Y up / Z up | U |
| Fullscreen | Fullscreen button or F |

Shortcuts apply when the canvas or page has focus, leaving form controls' keys
available normally. The controls remain visible while paused or focused with the
keyboard.

## Format and rendering

The reader supports the repository's [FTGS v1 layout](../README.md#ftgs-ply-layout-version-1):
binary little-endian float32 PLY, normalized time, and SH degrees 0–3. It
reconstructs anisotropic covariance from log-scales and wxyz rotations, evaluates
SH color, temporal opacity and optional velocity, and sorts animated centers
back to front in a worker before drawing splats. Ordinary static PLY
and `.pt` checkpoints are not inputs to this player.

The frame count comes from `n_frames` when present. Playback defaults to 30 fps,
which is a player setting rather than information stored in FTGS v1. For files
without a frame count, the default is 300; `?frames=120` overrides it. Frame `i`
maps to normalized time `i / max(n_frames - 1, 1)`.

The default point limit is **1 million**, sampled evenly throughout the file.
A brief loading notice reports when sampling is active. URL options keep the
view free of settings panels: `?points=all` loads the complete model,
`?points=250000` lowers the limit, `?resolution=half` lowers render resolution,
and `?up=z` starts with Z up. Combine options with `&` after the first `?`.
Large files still need enough browser memory to load the file and its selected
points; this version does not stream models. GPU texture limits and available
memory may prevent very large models loading.

This is a preview renderer, not a pixel-identical implementation of the CUDA
trainer. Depth sorting uses 65,536 bins; the rasterizer truncates Gaussians at
three standard deviations and drops contributions below 1/255. WebGL2 and browser
hardware acceleration are required. Camera fitting uses the central 98% of
canonical positions to avoid distant reconstruction outliers.

## Development checks

Parser, covariance, temporal sorting and demo checks use Node's built-in runner
(Node 20+), without installing packages:

```bash
node --test player/tests/*.test.mjs
```

An optional browser smoke check exercises rendering, seeking, orbiting, looping,
file-error recovery, URL loading and the mobile layout. It needs Python's
`playwright` and `Pillow` packages plus Playwright Chromium. These are test tools,
not player dependencies:

```bash
uv run --no-project --with playwright --with pillow python -m playwright install chromium
uv run --no-project --with playwright --with pillow python player/tests/browser_smoke.py
```

Pass `--browser /path/to/chromium` to use an existing browser, `--model
/path/to/scene.ftgs.ply` to additionally check a real exported file through both
file and URL loading, and `--screenshots /tmp/ftgs-player` to save screenshots.
