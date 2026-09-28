import { FTGSPlayer } from "./player.js?v=6";
import { attachPlayerBridge } from "./bridge.js?v=6";
import { demoFile } from "./demo.js";
import { mountPlayerShell } from "./shell.js?v=6";

mountPlayerShell(document.getElementById("viewer"));
const $ = (id) => document.getElementById(id);
const query = new URLSearchParams(location.search);
const requestedFps = Number(query.get("fps"));
const fps =
  Number.isFinite(requestedFps) && requestedFps > 0 ? requestedFps : null;
const requestedPoints = Number(query.get("points"));
const maxPoints =
  query.get("points") === "all"
    ? Infinity
    : Number.isSafeInteger(requestedPoints) && requestedPoints > 0
      ? requestedPoints
      : 1000000;
const requestedFrames = Number(query.get("frames"));
const requestedRate = Number(query.get("speed"));
const requestedVolume = Number(query.get("volume"));
const showControls = query.get("controls") !== "0";
const events = new AbortController();
const options = { signal: events.signal };
let player, detachBridge, idleTimer, messageTimer;
let arSupported = false;
let up = query.get("up") === "z" ? "z" : "y";

const clock = (seconds) => {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const tail = String(total % 60).padStart(2, "0");
  return minutes < 60
    ? `${minutes}:${tail}`
    : `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${tail}`;
};
function previewTime(fraction) {
  if ($("timeline").disabled) return;
  const time = Math.max(0, Math.min(1, fraction));
  const width = $("timeline").getBoundingClientRect().width;
  $("timeline-area").style.setProperty(
    "--preview",
    `${Math.max(24, Math.min(width - 24, time * width))}px`,
  );
  $("timeline-area").dataset.preview = "true";
  $("seek-preview").textContent = clock(time * player.duration);
}
function updateFullscreen() {
  const fullscreen = Boolean(document.fullscreenElement);
  const label = fullscreen ? "Exit fullscreen" : "Fullscreen";
  $("fullscreen").setAttribute("aria-label", label);
  $("fullscreen").dataset.tooltip = `${label} (F)`;
  $("fullscreen-icon").setAttribute(
    "href",
    fullscreen ? "#collapse" : "#expand",
  );
}
function wakeControls() {
  clearTimeout(idleTimer);
  $("viewer").classList.remove("idle");
  if (
    player?.playing &&
    player.state.status !== "loading" &&
    player.state.ar.status === "inactive"
  )
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
    `Frame ${state.frameIndex + 1} of ${state.nFrames}`,
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
  $("play-toggle").dataset.tooltip = player.playing
    ? "Pause (Space)"
    : "Play (Space)";
  wakeControls();
}
function updateAudio(state) {
  const audio = state.audio;
  $("audio-controls").hidden = !audio.available;
  const blocked = audio.status === "blocked";
  const silent =
    audio.muted || audio.volume === 0 || blocked || audio.status === "error";
  const label =
    audio.status === "error"
      ? "Audio unavailable"
      : blocked
        ? "Enable audio"
        : silent
          ? "Unmute"
          : "Mute";
  $("mute-toggle").disabled =
    audio.status === "error" || state.status === "loading";
  $("mute-toggle").setAttribute("aria-label", label);
  $("mute-toggle").setAttribute("aria-pressed", String(audio.muted));
  $("mute-toggle").dataset.tooltip = audio.error ?? `${label} (M)`;
  $("audio-icon").setAttribute("href", silent ? "#silent" : "#sound");
  $("volume").value = audio.volume;
}
function updateRate(state) {
  const select = $("playback-rate");
  select.querySelector("[data-custom]")?.remove();
  if (
    ![...select.options].some(
      (option) => Number(option.value) === state.playbackRate,
    )
  ) {
    const option = new Option(
      `${state.playbackRate}×`,
      String(state.playbackRate),
    );
    option.dataset.custom = "true";
    select.add(option);
  }
  select.value = state.playbackRate;
  select.disabled = !state.playable || state.status === "loading";
}
function updateAR() {
  const state = player.state;
  const { ar } = state;
  const active = ar.status === "presenting";
  $("viewer").dataset.ar = ar.status;
  $("ar").hidden = !arSupported;
  $("ar").disabled =
    !state.loaded || state.status === "loading" || ar.status === "starting";
  $("ar").setAttribute("aria-pressed", String(active));
  $("ar").setAttribute("aria-label", active ? "Exit AR" : "View in AR");
  $("ar").dataset.tooltip = active ? "Exit AR" : "View in AR";
  $("reset-view").setAttribute(
    "aria-label",
    active ? "Reposition" : "Reset view",
  );
  $("reset-view").dataset.tooltip = active
    ? "Reposition (R)"
    : "Reset view (R)";
  $("ar-hint").hidden = !active;
  $("ar-hint").textContent = ar.placed
    ? "Placed · Reset to reposition"
    : ar.surface
      ? "Tap to place on this surface"
      : ar.hitTest
        ? "Point at a surface, then tap to place"
        : "Tap to place in front of you";
  if (ar.error) status(ar.error, "error", 6000);
  else if (ar.status !== "inactive") status();
  wakeControls();
}
async function checkAR() {
  arSupported = await FTGSPlayer.isARSupported();
  if (!events.signal.aborted) updateAR();
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
    autoplay:
      query.get("autoplay") !== "0" && $("viewer").dataset.autoplay !== "false",
    loop: query.has("loop") ? query.get("loop") !== "0" : null,
    playbackRate:
      requestedRate >= 0.25 && requestedRate <= 4 ? requestedRate : null,
    volume:
      query.has("volume") &&
      Number.isFinite(requestedVolume) &&
      requestedVolume >= 0 &&
      requestedVolume <= 1
        ? requestedVolume
        : null,
    muted: query.get("muted") === "1",
  });
  player.addEventListener(
    "loadstart",
    ({ detail }) => {
      $("empty").hidden = true;
      $("play-toggle").disabled =
        $("timeline").disabled =
        $("reset-view").disabled =
          true;
      delete $("timeline-area").dataset.preview;
      status("Opening…", "loading");
      updateRate(detail);
      updateAudio(detail);
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
        `${detail.name}. Drag to orbit, Shift-drag to pan, scroll to zoom. WASD to move, Q/E down/up, Shift to move faster.`,
      );
      $("viewer").dataset.loaded = "true";
      $("controls").hidden = !showControls;
      $("play-toggle").disabled = !detail.playable;
      $("timeline").disabled = !detail.playable;
      $("reset-view").disabled = false;
      updateTime(detail);
      updatePlaying();
      updateRate(detail);
      updateAudio(detail);
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
      $("play-toggle").disabled = $("timeline").disabled = !detail.playable;
      $("reset-view").disabled = !detail.loaded;
      status(detail.error, "error");
      updateRate(detail);
      updateAudio(detail);
    },
    options,
  );
  player.addEventListener(
    "abort",
    ({ detail }) => {
      $("empty").hidden = detail.loaded;
      $("play-toggle").disabled = $("timeline").disabled = !detail.playable;
      $("reset-view").disabled = !detail.loaded;
      status("", detail.status);
      updateRate(detail);
      updateAudio(detail);
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
  for (const type of ["arstatechange", "loadstart", "loaded", "error", "abort"])
    player.addEventListener(type, updateAR, options);
  void checkAR();
  navigator.xr?.addEventListener("devicechange", checkAR, options);
  detachBridge = attachPlayerBridge(player, query.get("parentOrigin"));
  player.addEventListener(
    "audiochange",
    ({ detail }) => updateAudio(detail),
    options,
  );
  player.addEventListener(
    "ratechange",
    ({ detail }) => updateRate(detail),
    options,
  );
  $("mute-toggle").addEventListener(
    "click",
    () => {
      const audio = player.state.audio;
      if (audio.volume === 0) player.setVolume(1);
      player.setMuted(
        audio.status === "blocked" || audio.volume === 0 ? false : !audio.muted,
      );
    },
    options,
  );
  $("volume").addEventListener(
    "input",
    () => {
      player.setVolume(Number($("volume").value));
      player.setMuted(false);
    },
    options,
  );
  $("playback-rate").addEventListener(
    "change",
    () => player.setPlaybackRate(Number($("playback-rate").value)),
    options,
  );

  $("ar").addEventListener(
    "click",
    async () => {
      try {
        if (player.state.ar.status !== "inactive") await player.exitAR();
        else {
          const size = Number(query.get("arSize"));
          await player.enterAR({
            overlayRoot: $("viewer"),
            size: Number.isFinite(size) && size > 0 ? size : 1,
          });
        }
      } catch (error) {
        if (error.name !== "AbortError") status(error.message, "error", 6000);
      }
    },
    options,
  );
  $("controls").addEventListener(
    "beforexrselect",
    (event) => event.preventDefault(),
    options,
  );

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
      if (
        document.activeElement === $("timeline") ||
        $("timeline").matches(":hover")
      )
        previewTime(player.time);
    },
    options,
  );
  $("timeline").addEventListener(
    "pointermove",
    (event) => {
      const bounds = $("timeline").getBoundingClientRect();
      previewTime((event.clientX - bounds.left) / bounds.width);
    },
    options,
  );
  $("timeline").addEventListener(
    "pointerleave",
    () => {
      delete $("timeline-area").dataset.preview;
    },
    options,
  );
  $("timeline").addEventListener(
    "pointerup",
    (event) => {
      if (event.pointerType !== "mouse")
        delete $("timeline-area").dataset.preview;
    },
    options,
  );
  $("timeline").addEventListener(
    "focus",
    () => previewTime(player.time),
    options,
  );
  $("timeline").addEventListener(
    "blur",
    () => {
      delete $("timeline-area").dataset.preview;
    },
    options,
  );
  $("reset-view").addEventListener("click", () => player.fitCamera(), options);
  document.addEventListener("fullscreenchange", updateFullscreen, options);
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
          (player.frameIndex + (event.key === "ArrowRight" ? 1 : -1)) /
            Math.max(1, player.state.nFrames - 1),
        );
      } else if (event.key.toLowerCase() === "r") player.fitCamera();
      else if (event.key.toLowerCase() === "m" && player.state.audio.available)
        $("mute-toggle").click();
      else if (
        event.key.toLowerCase() === "f" &&
        player.state.ar.status === "inactive"
      )
        $("fullscreen").click();
      else if (
        event.key.toLowerCase() === "o" &&
        player.state.ar.status === "inactive"
      )
        $("file").click();
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
  else if (query.get("demo") === "1" || $("viewer").dataset.demo === "true")
    void load(demoFile(), { name: "Kinetic ribbon" });
} catch (error) {
  teardown();
  $("empty").hidden = true;
  status(error.message, "error");
}
