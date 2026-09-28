# FTGS Player

A standalone browser player for Vanilla `.ftgs.ply` and packaged `.tsog` files.
Rendering uses native WebGL2, with depth sorting in a Web Worker. It runs
directly as a static site without a build step or runtime package installation.

From the repository root:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory player
```

Open <http://localhost:8765> and drop a `.ftgs.ply` or `.tsog` anywhere, or click
the empty player to choose a file. Playback starts automatically and uses the file's speed
and loop defaults when provided. The scene fills the window, with a full-width
timeline and playback controls along the bottom.
The controls fade during playback and return on movement or touch. Hover over
the timeline to preview a time; use the folder button to open another file.
Local files stay in the browser.

## Audio and playback defaults

A `.tsog` can carry one optional audio file plus clip duration, FPS, speed,
looping, and initial volume in its metadata. The player follows these defaults
and keeps audio aligned when playing, pausing, seeking, or looping. Use the
speed selector for 0.25×–4× playback and the sound button (M) to enable or mute
audio. Hover or focus the sound controls for the desktop volume slider.

To add a soundtrack to an existing continuous TSOG animation, run this from
the repository root with Node.js 22+:

```bash
node player/tools/package-tsog.mjs scene.tsog scene-with-audio.tsog \
  --audio soundtrack.mp3 --duration 10 --fps 30 --speed 1.25 --loop --volume 0.8
```

Drop the resulting `.tsog` into the player. The command preserves the encoded
Gaussian images and stores the audio bytes without transcoding. Omit `--audio`
to keep a track while changing defaults, or use `--remove-audio` to remove it.
Existing output files are never overwritten. This command updates an existing
TSOG version-4 package; it does not convert a `.ftgs.ply` into TSOG.

If audible autoplay is blocked, the scene continues and the sound button shows
**Enable audio**. Click it or press M to start sound at the current scene time.
Audio format support depends on the browser; a codec error leaves the scene
playable. A short soundtrack leaves silence at the end, and a longer one stops
at the end of the scene. Audio pauses with a hidden desktop tab or lost XR tracking.

See the [metadata and packaging guide](TSOG.md#audio-and-playback-metadata) for
JSON fields, timing rules, CLI recipes, and the JavaScript packaging API.
The default volume and playback rate can also be overridden through the
[embedding API](API.md#programmatic-audio-and-speed). Existing files without
these optional fields keep their previous behavior; `.ftgs.ply` stores no audio.

For an independent reader/writer, use the versioned
[TSOG Playback Profile 1 specification](TSOG-SPEC.md),
[metadata schema](tsog.schema.json), and
[synthetic conformance examples](tests/fixtures/tsog/CONFORMANCE.md).
New packages declare profile version 1; legacy v4 files remain supported.
Validate metadata and the container from the repository root with Node.js 22+:

```bash
node player/tools/validate-tsog.mjs scene.tsog --require-profile
```

Omit `--require-profile` for a legacy file. Pixel decoding and audio codec
support need the additional checks described in the specification.

## Embed in another project

The [embedding API](API.md) provides `FTGSPlayer` for your own canvas and
`FTGSEmbed` for an iframe, including the hosted player. Both expose `load`,
`play`, `pause`, `seek`, `setPlaybackRate`, `setMuted`, `setVolume`, camera
controls, lifecycle events, and `destroy`.
Canvas instances are independent; iframe commands work across origins.
See the runnable [canvas example](examples/embedding.html) and
[iframe example](examples/embedding.html?mode=iframe), plus the React lifecycle
example in the API guide. No runtime dependencies are required.

## Files and model links

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
initial view than fitting all points. A model link can include a positive `fps`
(such as `fps=24` or `fps=60`) and a URL-encoded JSON `view` parameter, for example:

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

## URL options

Append query parameters to the player URL, joining them with `&`. For example:

```text
?src=scene.tsog&speed=1.5&loop=0&volume=0.6
```

`src` resolves relative to the player page. URL-encode a full source URL or saved
view, for example with `URLSearchParams` as above.

| Parameter | Value / default |
| --- | --- |
| `src` | HTTP(S) URL or relative path to a `.ftgs.ply` or `.tsog`. |
| `demo=1` | Load the generated ribbon when `src` is absent. |
| `autoplay=0` | Start paused; otherwise start after loading. Audible autoplay may need a click. |
| `loop=0` / `loop=1` | Override looping; omit to use the package default, or true if absent. |
| `speed` | Playback multiplier from 0.25 to 4; omit to use package `playback.rate`, or 1. |
| `muted=1` | Start with sound muted; default 0. |
| `volume` | Audio volume from 0 to 1; omit to use package `audio.volume`, or 1. |
| `fps` | Positive animation frame rate override; omit to use file timing, or 30. |
| `frames` | Positive display frame count override for continuous models; discrete/static TSOG retains its native count. |
| `controls=0` | Hide the transport; drag/drop and camera interaction remain available. |
| `points` | Positive point limit or `all`; default 1000000. |
| `resolution=half` | Render at half resolution; default is full resolution. |
| `up` | Camera up axis, `y` (default) or `z`. |
| `view` | URL-encoded JSON camera bookmark from the example above. |
| `arSize` | Positive AR scene diameter in meters; default 1. |

Use `speed` to play faster or slower while keeping the clip's duration in media
seconds. `fps` and `frames` instead change the animation's base timing, so they
can change its alignment with audio. Omit timing overrides to honor `.tsog`
metadata. A static scene keeps its declared duration.

URL options and player controls apply to the current player instance. To save
new defaults in the file, use the packaging command above. For an iframe with
audio, keep controls visible so users can enable sound inside the player.

## Controls

| Action | Control |
| --- | --- |
| Play / pause | Play button or Space |
| Playback speed | Speed selector (0.25×–4×) |
| Mute / enable audio | Sound button or M, when the file has audio |
| Volume | Hover or focus the sound controls on desktop |
| Seek / step | Timeline, or Left / Right for one frame |
| Open another file | Folder button, O, or drop anywhere |
| Orbit | Left drag / one-finger drag |
| Pan | Right drag, Shift + drag, or two-finger drag |
| Zoom | Scroll / pinch |
| Move forward / backward | W / S |
| Strafe left / right | A / D |
| Move down / up | Q / E |
| Move faster | Hold Shift (4× speed) |
| Fit camera / restore saved view | Reset view button or R |
| Switch Y up / Z up | U |
| Fullscreen | Fullscreen button or F |
| Enter / exit AR | AR button, shown on supported devices |

Shortcuts apply when the canvas or page has focus, leaving form controls' keys
available normally. The controls remain visible while paused or focused with the
keyboard.

Click or Tab into the scene to use movement keys. Movement follows your viewing
direction, with speed scaled to the scene and zoom level; Q/E follows the Y or Z
up axis. The keys work during playback and while paused. Releasing a key, focusing
another control, or leaving the tab stops movement. Browser shortcuts and text
fields keep their usual behavior. Movement uses the physical WASD/QE key positions.

## View in AR

On a device and browser that support WebXR immersive AR, open the player over
HTTPS, load a file, and press **AR**. Point at a surface and tap to place the
scene. If surface detection is unavailable, tap to place it in front of you.
Move your device to look around. The scene starts at about one meter across;
add `?arSize=0.5` for a smaller preview, or set `size` through the [canvas API](API.md#webxr-ar).

Playback, seeking, speed changes, and embedded audio work in AR. Audio and
scene time pause together when tracking is unavailable. Where the browser supports HTML overlays, the
same compact controls stay visible: **Reset** repositions and **AR** exits.
Otherwise, use the browser's system exit control. Exiting restores the desktop
view and keeps the playback position. Devices without immersive AR keep the
normal player. GitHub Pages provides HTTPS; a plain HTTP LAN address cannot
enable WebXR. Localhost is allowed for development. Iframe hosts must delegate
`xr-spatial-tracking`; use `allow="autoplay; xr-spatial-tracking"` when also
playing audio. See the [embedding guide](API.md#webxr-ar).

## Format and rendering

The reader supports the repository's [FTGS v1 layout](../README.md#ftgs-ply-layout-version-1):
binary little-endian float32 PLY, normalized time, and SH degrees 0–3. It
reconstructs anisotropic covariance from log-scales and wxyz rotations, evaluates
SH color, temporal opacity and optional velocity, and sorts animated centers
back to front in a worker before drawing splats. Ordinary static PLY
and `.pt` checkpoints are not inputs to this player.

For `.ftgs.ply`, the frame count comes from `n_frames` when present. Playback
defaults to 30 fps,
which is a player setting rather than information stored in FTGS v1. For files
without a frame count, the default is 300; `?frames=120` overrides it. Frame `i`
maps to normalized time `i / max(n_frames - 1, 1)`.

Packaged **TSOG version 4** is also supported, including continuous motion,
discrete frame sequences and static models. Drop one `.tsog` ZIP package;
the player decodes its WebP attributes locally. Discrete files use their own
frame count and FPS unless FPS is explicitly overridden. See [TSOG support](TSOG.md)
for the layout, timing assumptions and limits, and for the citation to
[Gmira et al.'s TSOG paper](https://arxiv.org/abs/2607.28049) and
[Xiaomi Research's original repository](https://github.com/xiaomi-research/tsog).
The original Clear BSD and PlayCanvas MIT license notices are retained in
[THIRD_PARTY.md](THIRD_PARTY.md).

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
(Node 22+), without installing packages:

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

The API integration check covers independent canvases, cross-origin iframe
commands, lifecycle events, cancellation, cleanup, and remounting:

```bash
uv run --no-project --with playwright python player/tests/browser_api.py
```

The XR check uses a simulated device with the real WebGL renderer and sorting
worker. It covers stereo/mono views, transparency, current-pose rendering,
placement, playback, failure recovery, teardown, UI and iframe entry:

```bash
uv run --no-project --with playwright python player/tests/browser_xr.py
```

Physical-device testing is still needed to check camera passthrough, surface
tracking, and performance on the target hardware.

The TSOG check covers attribute decoding, continuous/discrete/static scenes,
frame boundaries, file drop, and iframe loading:

```bash
uv run --no-project --with playwright --with pillow python player/tests/browser_tsog.py
```

The packaged-audio check generates its own synthetic tone and tests file
metadata, media synchronization, autoplay handling, audio controls and cleanup:

```bash
uv run --no-project --with playwright python player/tests/browser_audio.py
```

All browser checks accept `--browser /path/to/chromium`. Published example data
consists only of synthetic fixtures; audio tests generate their tones in memory.
