import { FTGSPlayer } from "./player.js";
import { attachPlayerBridge } from "./bridge.js";
import { demoFile } from "./demo.js";

const $ = (id) => document.getElementById(id);
const query = new URLSearchParams(location.search);
const fps = [24, 30, 60].includes(Number(query.get("fps")))
  ? Number(query.get("fps"))
  : 30;
const requestedPoints = Number(query.get("points"));
const maxPoints =
  query.get("points") === "all"
    ? Infinity
    : Number.isSafeInteger(requestedPoints) && requestedPoints > 0
      ? requestedPoints
      : 1000000;
const requestedFrames = Number(query.get("frames"));
const showControls = query.get("controls") !== "0";
const events = new AbortController();
const options = { signal: events.signal };
let player, detachBridge, idleTimer, messageTimer;
let up = query.get("up") === "z" ? "z" : "y";

const clock = (seconds) => {
  const tenths = Math.round(seconds * 10);
  return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, "0")}`;
};
function wakeControls() {
  clearTimeout(idleTimer);
  $("viewer").classList.remove("idle");
  if (player?.playing && player.state.status !== "loading")
    idleTimer = setTimeout(() => $("viewer").classList.add("idle"), 1800);
}
function status(text = "", state = "ready", timeout = 0) {
  clearTimeout(messageTimer);
  $("viewer").dataset.state = state;
  $("viewer").setAttribute("aria-busy", String(state === "loading"));
  $("message").dataset.state = state;
  $("message").textContent = text;
  $("message").hidden = !text;
  if (timeout)
    messageTimer = setTimeout(() => {
      $("message").hidden = true;
    }, timeout);
}
function updateTime(state) {
  $("timeline").value = state.time;
  $("timeline").style.setProperty("--progress", `${state.time * 100}%`);
  $("timeline").setAttribute(
    "aria-valuetext",
    `Frame ${Math.round(state.time * (state.nFrames - 1)) + 1} of ${state.nFrames}`,
  );
  $("timecode").replaceChildren(
    document.createTextNode(clock(state.currentTime)),
    Object.assign(document.createElement("span"), {
      textContent: `/ ${clock(state.duration)}`,
    }),
  );
}
function updatePlaying() {
  $("play-toggle").setAttribute(
    "aria-label",
    player.playing ? "Pause" : "Play",
  );
  $("play-icon").setAttribute("href", player.playing ? "#pause" : "#play");
  wakeControls();
}
async function load(input, loadOptions) {
  if (!player) return;
  try {
    await player.load(input, loadOptions);
    $("canvas").focus({ preventScroll: true });
  } catch {
    /* The player's error event displays failures; cancelled loads stay silent. */
  }
}
function teardown() {
  events.abort();
  clearTimeout(idleTimer);
  clearTimeout(messageTimer);
  detachBridge?.();
  player?.destroy();
}

try {
  player = new FTGSPlayer($("canvas"), {
    fps,
    maxPoints,
    up,
    resolution: query.get("resolution") === "half" ? 0.5 : 1,
    frames:
      Number.isSafeInteger(requestedFrames) && requestedFrames > 0
        ? requestedFrames
        : null,
    autoplay: query.get("autoplay") !== "0",
    loop: query.get("loop") !== "0",
  });
  player.addEventListener(
    "loadstart",
    () => {
      $("empty").hidden = true;
      status("Opening…", "loading");
    },
    options,
  );
  player.addEventListener(
    "progress",
    ({ detail }) => {
      status(`Opening · ${Math.round(detail.progress * 100)}%`, "loading");
    },
    options,
  );
  player.addEventListener(
    "loaded",
    ({ detail }) => {
      document.title = `${detail.name} · FTGS Player`;
      $("canvas").setAttribute(
        "aria-label",
        `${detail.name}. Drag to orbit, Shift-drag to pan, scroll to zoom.`,
      );
      $("viewer").dataset.loaded = "true";
      $("controls").hidden = !showControls;
      $("play-toggle").disabled = detail.nFrames === 1;
      $("timeline").disabled = detail.nFrames === 1;
      updateTime(detail);
      updatePlaying();
      status(
        detail.pointCount < detail.sourceCount
          ? `Previewing ${detail.pointCount.toLocaleString()} of ${detail.sourceCount.toLocaleString()} Gaussians.`
          : "",
        "ready",
        5000,
      );
    },
    options,
  );
  player.addEventListener(
    "error",
    ({ detail }) => {
      $("controls").hidden = !showControls;
      status(detail.error, "error");
    },
    options,
  );
  player.addEventListener(
    "abort",
    ({ detail }) => {
      $("empty").hidden = detail.loaded;
      status("", detail.status);
    },
    options,
  );
  player.addEventListener(
    "timeupdate",
    ({ detail }) => updateTime(detail),
    options,
  );
  for (const type of ["play", "pause"])
    player.addEventListener(type, updatePlaying, options);
  detachBridge = attachPlayerBridge(player, query.get("parentOrigin"));

  for (const id of ["empty", "open-file"])
    $(id).addEventListener("click", () => $("file").click(), options);
  $("file").addEventListener(
    "change",
    () => {
      const file = $("file").files[0];
      if (file) void load(file);
      $("file").value = "";
    },
    options,
  );
  $("play-toggle").addEventListener(
    "click",
    () => {
      if (player.playing) player.pause();
      else player.play();
    },
    options,
  );
  $("timeline").addEventListener(
    "input",
    () => {
      player.pause();
      player.seek(Number($("timeline").value));
    },
    options,
  );
  $("fullscreen").addEventListener(
    "click",
    async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await $("viewer").requestFullscreen();
      } catch {
        status("Fullscreen is unavailable in this browser.", "error");
      }
    },
    options,
  );
  $("controls").addEventListener(
    "click",
    (event) => {
      if (event.detail > 0) event.target.closest("button")?.blur();
    },
    options,
  );
  for (const name of ["pointermove", "pointerdown"])
    $("viewer").addEventListener(name, wakeControls, options);
  window.addEventListener(
    "keydown",
    (event) => {
      if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.target.isContentEditable ||
        ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(event.target.tagName)
      )
        return;
      wakeControls();
      if (event.code === "Space") {
        event.preventDefault();
        $("play-toggle").click();
      } else if (
        player.state.loaded &&
        ["ArrowLeft", "ArrowRight"].includes(event.key)
      ) {
        event.preventDefault();
        player.pause();
        player.seek(
          player.time +
            (event.key === "ArrowRight" ? 1 : -1) /
              Math.max(1, player.state.nFrames - 1),
        );
      } else if (event.key.toLowerCase() === "r") player.fitCamera();
      else if (event.key.toLowerCase() === "f") $("fullscreen").click();
      else if (event.key.toLowerCase() === "o") $("file").click();
      else if (event.key.toLowerCase() === "u") {
        up = up === "y" ? "z" : "y";
        player.setUpAxis(up);
        status(`${up.toUpperCase()} up`, "ready", 1500);
      }
    },
    options,
  );
  window.addEventListener(
    "dragover",
    (event) => {
      event.preventDefault();
      if (event.dataTransfer.types.includes("Files")) {
        event.dataTransfer.dropEffect = "copy";
        $("drop-hint").hidden = false;
      }
    },
    options,
  );
  window.addEventListener(
    "dragleave",
    (event) => {
      if (!event.relatedTarget) $("drop-hint").hidden = true;
    },
    options,
  );
  window.addEventListener(
    "drop",
    (event) => {
      event.preventDefault();
      $("drop-hint").hidden = true;
      const file = event.dataTransfer.files[0];
      if (file) void load(file);
    },
    options,
  );
  window.addEventListener(
    "blur",
    () => {
      $("drop-hint").hidden = true;
    },
    options,
  );
  window.addEventListener(
    "pagehide",
    (event) => {
      if (!event.persisted) teardown();
    },
    options,
  );

  const url = query.get("src");
  if (url)
    void load(url, {
      view: query.has("view") ? JSON.parse(query.get("view")) : null,
    });
  else if (query.get("demo") === "1")
    void load(demoFile(), { name: "Kinetic ribbon" });
} catch (error) {
  teardown();
  $("empty").hidden = true;
  status(error.message, "error");
}
