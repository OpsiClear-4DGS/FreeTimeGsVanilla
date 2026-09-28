# Embedding the FTGS player

Use `FTGSPlayer` for a canvas inside your own UI, or `FTGSEmbed` to control the
standalone page in an iframe. Both support loading `.ftgs.ply` and packaged `.tsog`
files, playback, seeking,
events, and cleanup. Neither needs a runtime package or a build step.

Runnable examples: [canvas](examples/embedding.html) ·
[iframe](examples/embedding.html?mode=iframe). Both use generated demo data.
The examples use the same minimal player controls as the standalone page. The
canvas example binds the canvas API through `app.js`; the iframe example's
[source](examples/embedding.js) loads the model through the iframe API and uses
the child player's built-in controls.

## Canvas API

Serve the player modules from your project, keeping their relative paths intact.
Import `player.js`; do not import `app.js` or the standalone page's stylesheet.
The canvas controller does not add UI, change the document title, capture global
keyboard shortcuts, or style your page. Multiple canvases work independently.

```html
<canvas id="scene" tabindex="0" aria-label="Animated scene"
  style="display:block;width:100%;height:480px;touch-action:none"></canvas>
<script type="module">
  import { FTGSPlayer } from "./ftgs-player/player.js";

  const player = new FTGSPlayer(document.querySelector("#scene"), {
    autoplay: false,
    loop: true,
    fps: 30,
  });
  player.addEventListener("timeupdate", ({ detail }) => {
    console.log(detail.time, detail.currentTime, detail.duration);
  });
  player.addEventListener("error", ({ detail }) => console.error(detail.error));

  await player.load("./assets/scene.ftgs.ply"); // Also accepts a File or Blob.
  player.seek(0.5); // Normalized time: 0 = start, 1 = end.
  player.play();
  // player.pause();
  // player.destroy(); // Call when your component unmounts.
</script>
```

Give the canvas a CSS width and height. Rendering follows element resizing.
Drag, pan, zoom, and keyboard movement stay local to that canvas; build playback
buttons or shortcuts in your host application. After clicking or tabbing into
the canvas, hold W/S to move forward/back, A/D to strafe, Q/E to move down/up, and
Shift to move four times faster. Speed follows scene scale and zoom; Q/E respects
the configured up axis. Movement also works while paused and stops on blur or
when the page is hidden. Physical key positions are used. The player makes the
canvas focusable if needed and restores its original `tabindex` on destruction.
Keep `touch-action:none` for touch navigation.

Constructor options:

| Option | Default | Meaning |
| --- | --- | --- |
| `autoplay` | `true` | Start after loading, when the document is visible. |
| `loop` | `true` | Repeat playback; otherwise stop at the end. |
| `fps` | `null` | Use package FPS, or 30 if absent. A positive number overrides it. |
| `frames` | File metadata, or `300` | Override continuous display frame count. Discrete/static TSOG keeps its native frame count. |
| `maxPoints` | `1000000` | Positive point limit, or `Infinity` for all points. |
| `resolution` | `1` | Render scale greater than zero and at most one. |
| `up` | `"y"` | Camera up axis: `"y"` or `"z"`. |

## Methods and events

| Method | Behavior |
| --- | --- |
| `await load(source, options?)` | Load a `File`, `Blob`, URL string, or `URL`. Resolves with a state snapshot once the model is ready; drawing follows on the next animation/worker frame. |
| `play()` | Start or resume. At time `1`, restart from `0`. A single-frame model stays paused. |
| `pause()` | Pause at the current time. |
| `seek(time)` | Seek to normalized time, clamped to `[0, 1]`. Preserve the playing/paused state. Non-finite values throw. |
| `setView(view)` | Set and bookmark `{ eye: [x,y,z], target: [x,y,z], up: "y" or "z", fov: 45 }`. |
| `fitCamera()` | Restore the bookmarked view, or fit the loaded model. In AR, restart placement. |
| `setUpAxis(up)` | Canvas API only: change up axis and fit the model, clearing the bookmark. |
| `await FTGSPlayer.isARSupported()` | Canvas API static method: check immersive AR availability without requesting a session. |
| `await enterAR(options?)` | Canvas API only: start AR from a user click or tap. Options: `size` in meters (default `1`) and optional `overlayRoot` element. |
| `await exitAR()` | End AR and return to the desktop camera. Available on both APIs. |
| `destroy()` | Release resources. Safe to call more than once. |

`load()` options are `name`, `view`, and `autoplay` (override the constructor
default for that load). The canvas API also accepts an `AbortSignal` as `signal`.
Starting another load or destroying the player cancels a pending load, which
rejects with `AbortError`. Catch load rejections in your application. Invalid
files leave the previously loaded model available, paused; a subsequent load
can recover. `play()`, `pause()`, and valid `seek()` calls do nothing before a
model is loaded.

`player.state` is a snapshot with `status`, `loaded`, `playing`, `time`,
`currentTime`, `duration`, `fps`, `loop`, `nFrames`, `frameIndex`, `format`, `timeline`,
`name`, `pointCount`,
`sourceCount`, `progress`, `error`, and `ar`. `time` is normalized; `currentTime` and
`duration` are seconds. `progress` is parsing progress from zero to one, not
download progress. `error` is a message or `null`. Status is `empty`, `loading`,
`ready`, `error`, or `destroyed`. The canvas API also has read-only `time`,
`playing`, `duration`, `fps`, and `frameIndex` getters.

`format` is `"ftgs-ply"`, `"tsog"`, or `null` before loading. `timeline` is
`"continuous"`, `"discrete"`, `"static"`, or `null`. `frameIndex` is zero-based.
For discrete TSOG, duration is `nFrames / fps` and `seek(t)` selects
`min(nFrames - 1, floor(t * nFrames))`; the last frame gets a full playback
interval. Continuous models keep their existing normalized-time playback.
Omit `fps` to honor package metadata. See [TSOG.md](TSOG.md) for supported
encodings, the original paper/repository, and timing assumptions.

Subscribe with `addEventListener`. Every event's `detail` contains a state snapshot:
`loadstart`, `progress`, `loaded`, `play`, `pause`, `timeupdate`, `ended`, `abort`,
`error`, `arstatechange`, and `destroy`. Playback time events are limited to about ten per second;
explicit seeks emit immediately. `ended` fires when playback stops with looping
disabled. Hiding the desktop document pauses playback. `destroy()` ends AR, cancels downloads,
terminates the sorting worker, removes internal listeners and resize observers,
stops animation, and releases GPU resources. Your canvas remains in the DOM and
can be reused by a new player.

## WebXR AR

Load a model, check availability, then request a session directly from a user
gesture. Keep the availability check outside the click handler so it does not
consume the gesture before `enterAR()` requests the session:

```js
const button = document.querySelector("#ar");
button.hidden = !(await FTGSPlayer.isARSupported());
button.onclick = () => {
  const action = player.state.ar.status === "inactive"
    ? player.enterAR({ size: 1 })
    : player.exitAR();
  action.catch(error => console.error(error.message));
};
player.addEventListener("arstatechange", ({ detail }) => {
  button.textContent = detail.ar.status === "presenting" ? "Exit AR" : "AR";
});
```

`size` sets the diameter of the model's fitted bounds in meters; files need no
world-scale metadata. The model's configured Y/Z up axis is respected. Point
the device at a surface and tap (or use a controller's select action) to place
the preview. Surface hit testing is optional; without it, the preview appears
1.5 meters ahead of the viewer. `fitCamera()` restarts placement. Exiting AR
preserves the desktop camera and current playback time. Loading another model
or destroying the player ends the session, including a pending entry request.

`state.ar` contains `status` (`inactive`, `starting`, or `presenting`), `placed`,
`hitTest` (surface detection available), `surface` (current preview found a
surface), and `error` (message or `null`). Failures reject `enterAR()` and report
through `arstatechange`; they do not change a loaded model's normal status.

To retain custom HTML controls during AR, pass their container as `overlayRoot`.
DOM overlay support is optional. Keep its background transparent and keep the
desktop canvas outside that container (or hide it while presenting). Cancel
`beforexrselect` on interactive controls so pressing them does not also place the
scene. On devices without overlays, use the browser's system control to exit AR.
The core API adds no controls or document styles.

AR requires a compatible device/browser and a secure context (HTTPS or
localhost). A plain HTTP LAN address will not enable it. Iframes also require
`allow="xr-spatial-tracking"`, permitted by the parent's Permissions Policy.
The browser handles the permission prompt. See the
[WebXR session requirements](https://developer.mozilla.org/en-US/docs/Web/API/XRSystem/requestSession).

XR uses current device poses and each eye's projection/viewport, with transparent
premultiplied-alpha rendering. Both eyes share the most recent center-view sort;
the current tracked pose is rendered even while the sorting worker is busy.
Playback advances on XR frames and freezes while tracking is unavailable.
Placement lasts for the current session; persistent anchors and real-world depth
occlusion are not implemented.

## Iframe API

Import the small `embed.js` helper in the parent project. You can copy that file
alone or import it from the hosted site. Append an iframe, then construct its
controller. The helper loads the player page and configures the connection.

```html
<iframe id="scene" title="FTGS player" allowfullscreen
  style="width:100%;height:480px;border:0"></iframe>
<script type="module">
  import { FTGSEmbed } from "https://opsiclear-4dgs.github.io/ftgs-player/embed.js";

  const player = new FTGSEmbed(document.querySelector("#scene"), {
    src: "https://opsiclear-4dgs.github.io/ftgs-player/?controls=0&autoplay=0",
  });
  await player.ready;
  await player.load("./assets/scene.ftgs.ply");
  await player.seek(0.5);
  await player.play();
  // await player.pause();
  // player.destroy();
</script>
```

Iframe commands return promises; await them when ordering matters. Methods and
events match the canvas API except `setUpAxis`, `enterAR`, the static support
check, and `load`'s `signal` option. For AR, add `allow="xr-spatial-tracking"`
to the iframe, keep the built-in controls enabled (omit `controls=0`), and press
the AR button inside the player. Entry requires a user gesture in the child;
it is deliberately not exposed as a postMessage command. The host can observe
`arstatechange`, call `fitCamera()` to reposition, and `await exitAR()`.
`player.state` is the latest received snapshot, initially `null`;
`await player.getState()` requests a fresh one. A `File` or `Blob` can be passed
directly without uploading it. Relative URLs passed to the helper's `load()`
resolve against the **parent page**. URLs in the iframe page's `?src=` parameter
resolve against the **player page**. A model server on a different origin from
the iframe must allow CORS.

Page options include `controls=0` to hide the transport, `autoplay=0`, `loop=0`,
`arSize` (AR scene diameter in meters), and the existing `fps`, `frames`, `points`, `resolution`, `up`, `view`, `src`, and
`demo` options. Drag/drop and camera interaction still work with the transport
hidden. `FTGSEmbed` also accepts `timeout` (connection, default 15,000 ms) and
`requestTimeout` (each command, default 120,000 ms). A command timeout rejects the
request; call `destroy()` to cancel any remaining work in the iframe.

The helper adds an exact `parentOrigin` to the player URL. Control messages are
accepted only from that parent window and origin; replies check the child window,
origin, protocol version, and connection ID. The bridge is disabled without this
explicit setting. A plain iframe still works without any helper. Serve both
pages over HTTP(S); an opaque sandboxed iframe is not supported. If using a
sandbox, allow scripts and same-origin. Creating a controller reloads its iframe;
destroy the controller before reusing or navigating the element. `destroy()`
rejects pending requests, removes host listeners, and unloads the child to
`about:blank`, keeping the iframe element for the host to remove or reuse.

## React lifecycle

The same canvas API works inside a component, including mount/cleanup cycles:

```jsx
import { useEffect, useRef } from "react";
import { FTGSPlayer } from "./ftgs-player/player.js";

export function FTGSCanvas({ source }) {
  const canvas = useRef(null);
  useEffect(() => {
    const player = new FTGSPlayer(canvas.current);
    if (source) player.load(source).catch(error => {
      if (error.name !== "AbortError") console.error(error);
    });
    return () => player.destroy();
  }, [source]);
  return <canvas ref={canvas} tabIndex={0} aria-label="Animated scene"
    style={{ display: "block", width: "100%", height: 480, touchAction: "none" }} />;
}
```

Vue and other frameworks can use the same mount/destroy lifecycle. For direct
canvas hosting, retain `player.js`, `model.js`, `ftgs.js`, `tsog.js`, `zip.js`,
`webp.js`, `camera.js`, `renderer.js`, `xr.js`, `sort-worker.js`, and `sort.js`
together, or let your bundler process their module and worker URLs. Self-host these modules with your application so the module
worker can load from the same origin. WebGL2 and adequate GPU memory are required.

When redistributing the player modules, include the repository license and
[THIRD_PARTY.md](THIRD_PARTY.md), which preserves TSOG and PlayCanvas notices.
