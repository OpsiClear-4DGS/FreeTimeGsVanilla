# Embedding the FTGS player

Use `FTGSPlayer` for a canvas inside your own UI, or `FTGSEmbed` to control the
standalone page in an iframe. Both support loading files, playback, seeking,
events, and cleanup. Neither needs a runtime package or a build step.

Runnable examples: [canvas](examples/embedding.html) ·
[iframe](examples/embedding.html?mode=iframe). Both use generated demo data.

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
Drag, pan, and zoom stay local to that canvas; build any buttons or shortcuts in
your host application. Keep `touch-action:none` for touch navigation.

Constructor options:

| Option | Default | Meaning |
| --- | --- | --- |
| `autoplay` | `true` | Start after loading, when the document is visible. |
| `loop` | `true` | Repeat playback; otherwise stop at the end. |
| `fps` | `30` | Positive playback frame rate. |
| `frames` | File metadata, or `300` | Override the frame count with a positive integer. |
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
| `fitCamera()` | Restore the bookmarked view, or fit the loaded model. |
| `setUpAxis(up)` | Canvas API only: change up axis and fit the model, clearing the bookmark. |
| `destroy()` | Release resources. Safe to call more than once. |

`load()` options are `name`, `view`, and `autoplay` (override the constructor
default for that load). The canvas API also accepts an `AbortSignal` as `signal`.
Starting another load or destroying the player cancels a pending load, which
rejects with `AbortError`. Catch load rejections in your application. Invalid
files leave the previously loaded model available, paused; a subsequent load
can recover. `play()`, `pause()`, and valid `seek()` calls do nothing before a
model is loaded.

`player.state` is a snapshot with `status`, `loaded`, `playing`, `time`,
`currentTime`, `duration`, `fps`, `loop`, `nFrames`, `name`, `pointCount`,
`sourceCount`, `progress`, and `error`. `time` is normalized; `currentTime` and
`duration` are seconds. `progress` is parsing progress from zero to one, not
download progress. `error` is a message or `null`. Status is `empty`, `loading`,
`ready`, `error`, or `destroyed`. The canvas API also has read-only `time`,
`playing`, and `duration` getters.

Subscribe with `addEventListener`. Every event's `detail` contains a state snapshot:
`loadstart`, `progress`, `loaded`, `play`, `pause`, `timeupdate`, `ended`, `abort`,
`error`, and `destroy`. Playback time events are limited to about ten per second;
explicit seeks emit immediately. `ended` fires when playback stops with looping
disabled. Hiding the document pauses playback. `destroy()` cancels downloads,
terminates the sorting worker, removes internal listeners and resize observers,
stops animation, and releases GPU resources. Your canvas remains in the DOM and
can be reused by a new player.

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
events match the canvas API except `setUpAxis` and `load`'s `signal` option.
`player.state` is the latest received snapshot, initially `null`;
`await player.getState()` requests a fresh one. A `File` or `Blob` can be passed
directly without uploading it. Relative URLs passed to the helper's `load()`
resolve against the **parent page**. URLs in the iframe page's `?src=` parameter
resolve against the **player page**. A model server on a different origin from
the iframe must allow CORS.

Page options include `controls=0` to hide the transport, `autoplay=0`, `loop=0`,
and the existing `fps`, `frames`, `points`, `resolution`, `up`, `view`, `src`, and
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
canvas hosting, retain `player.js`, `ftgs.js`, `camera.js`, `renderer.js`,
`sort-worker.js`, and `sort.js` together, or let your bundler process their module
and worker URLs. Self-host these modules with your application so the module
worker can load from the same origin. WebGL2 and adequate GPU memory are required.
