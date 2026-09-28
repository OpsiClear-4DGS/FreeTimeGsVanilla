import { readFTGS } from "./ftgs.js";
import { OrbitCamera } from "./camera.js";
import { SplatRenderer } from "./renderer.js";
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
const resolution = query.get("resolution") === "half" ? 0.5 : 1;
const requestedFrames = Number(query.get("frames"));
const clock = (seconds) => {
  const tenths = Math.round(seconds * 10);
  return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, "0")}`;
};
let renderer, camera, worker, pending, model, source, loadController;
let bookmarkView, bookmarkURL, idleTimer, messageTimer;
let generation = 0,
  time = 0,
  playing = false,
  loading = false,
  dirty = true,
  raf;
let previousTick = performance.now();

function wakeControls() {
  clearTimeout(idleTimer);
  $("viewer").classList.remove("idle");
  if (playing && !loading)
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
function duration() {
  return Math.max(1, model.nFrames - 1) / fps;
}
function updateTime() {
  $("timeline").value = time;
  $("timeline").style.setProperty("--progress", `${time * 100}%`);
  $("timeline").setAttribute(
    "aria-valuetext",
    `Frame ${Math.round(time * (model.nFrames - 1)) + 1} of ${model.nFrames}`,
  );
  $("timecode").replaceChildren(
    document.createTextNode(clock(time * duration())),
    Object.assign(document.createElement("span"), {
      textContent: `/ ${clock(duration())}`,
    }),
  );
}
function setPlaying(value) {
  playing = Boolean(value && model && model.nFrames > 1 && !loading);
  $("play-toggle").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("play-icon").setAttribute("href", playing ? "#pause" : "#play");
  previousTick = performance.now();
  wakeControls();
}
function seek(value) {
  if (!model) return;
  time = Math.max(0, Math.min(1, value));
  dirty = true;
  updateTime();
}
function fitCamera() {
  if (!camera) return;
  if (bookmarkView && source?.url === bookmarkURL) camera.restore(bookmarkView);
  else camera.fit();
}
function connectWorker(data) {
  worker?.terminate();
  pending = null;
  const active = (worker = new Worker(
    new URL("./sort-worker.js", import.meta.url),
    { type: "module" },
  ));
  active.onmessage = ({ data: result }) => {
    if (worker !== active) return;
    const request = pending;
    pending = null;
    if (result.type === "error") {
      setPlaying(false);
      status(result.message, "error");
      return;
    }
    if (!request || loading) return;
    try {
      renderer.draw(
        result.order,
        request.camera,
        request.time,
        model.degree,
        resolution,
      );
    } catch (error) {
      setPlaying(false);
      status(error.message, "error");
    }
  };
  active.onerror = (event) => {
    pending = null;
    setPlaying(false);
    status(`Render worker failed: ${event.message}`, "error");
  };
  active.postMessage(
    {
      type: "model",
      model: {
        positionTime: data.positionTime,
        velocityDuration: data.velocityDuration,
        alpha: data.alpha,
        useVelocity: data.useVelocity,
        opacityFloor: data.opacityFloor,
      },
    },
    [data.positionTime.buffer, data.velocityDuration.buffer, data.alpha.buffer],
  );
}

async function load(input) {
  if (!renderer) return;
  const id = ++generation;
  loadController?.abort();
  loadController = new AbortController();
  const { signal } = loadController;
  loading = true;
  setPlaying(false);
  $("empty").hidden = true;
  status(typeof input === "string" ? "Downloading…" : "Opening…", "loading");
  try {
    let item = input;
    if (typeof item === "string") {
      const url = new URL(item, location.href);
      if (!["http:", "https:"].includes(url.protocol))
        throw new Error("Use an HTTP or HTTPS model URL.");
      const response = await fetch(url, { signal });
      if (!response.ok)
        throw new Error(`Model request failed: HTTP ${response.status}.`);
      item = {
        blob: await response.blob(),
        name:
          decodeURIComponent(url.pathname.split("/").pop()) || "Remote model",
        url: url.href,
      };
    }
    const data = await readFTGS(item.blob, {
      maxPoints,
      signal,
      onProgress: (progress) => {
        if (id === generation)
          status(`Opening · ${Math.round(progress * 100)}%`, "loading");
      },
    });
    if (id !== generation) return;
    renderer.setModel(data);
    model = {
      degree: data.degree,
      nFrames:
        Number.isSafeInteger(requestedFrames) && requestedFrames > 0
          ? requestedFrames
          : (data.nFrames ?? 300),
    };
    source = item;
    camera.fit(data.bounds);
    if (bookmarkView && item.url === bookmarkURL) camera.restore(bookmarkView);
    connectWorker(data);
    document.title = `${item.name} · FTGS Player`;
    $("canvas").setAttribute(
      "aria-label",
      `${item.name}. Drag to orbit, Shift-drag to pan, scroll to zoom.`,
    );
    $("viewer").dataset.loaded = "true";
    $("controls").hidden = false;
    $("play-toggle").disabled = model.nFrames === 1;
    $("timeline").disabled = model.nFrames === 1;
    loading = false;
    seek(0);
    setPlaying(true);
    const notice =
      data.count < data.sourceCount
        ? `Previewing ${data.count.toLocaleString()} of ${data.sourceCount.toLocaleString()} Gaussians.`
        : item.demo
          ? "Synthetic demo"
          : "";
    status(notice, "ready", 5000);
    $("canvas").focus({ preventScroll: true });
  } catch (error) {
    if (id !== generation || error.name === "AbortError") return;
    loading = false;
    dirty = true;
    // The file button remains available when the very first load fails.
    $("controls").hidden = false;
    status(
      error instanceof TypeError
        ? "Unable to load URL. Check the address, connection, and the host's CORS settings."
        : error.message,
      "error",
    );
  }
}

function tick(now) {
  const elapsed = Math.min((now - previousTick) / 1000, 0.25);
  previousTick = now;
  if (model && !loading) {
    if (playing) {
      time = (time + elapsed / duration()) % 1;
      dirty = true;
      updateTime();
    }
    if (dirty && !pending && worker) {
      dirty = false;
      pending = { time, camera: camera.snapshot() };
      worker.postMessage({
        type: "sort",
        time,
        view: pending.camera.view,
        near: pending.camera.near,
      });
    }
  }
  raf = requestAnimationFrame(tick);
}

for (const id of ["empty", "open-file"])
  $(id).onclick = () => $("file").click();
$("file").onchange = () => {
  const file = $("file").files[0];
  if (file) void load({ blob: file, name: file.name });
  $("file").value = "";
};
$("play-toggle").onclick = () => {
  if (!playing && time === 1) seek(0);
  setPlaying(!playing);
};
$("timeline").oninput = () => {
  setPlaying(false);
  seek(Number($("timeline").value));
};
$("fullscreen").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await $("viewer").requestFullscreen();
  } catch {
    status("Fullscreen is unavailable in this browser.", "error");
  }
};
// Mouse clicks need not keep the bar focused; keyboard focus keeps it visible.
$("controls").addEventListener("click", (event) => {
  if (event.detail > 0) event.target.closest("button")?.blur();
});
for (const name of ["pointermove", "pointerdown"])
  $("viewer").addEventListener(name, wakeControls);
window.addEventListener("keydown", (event) => {
  if (
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(event.target.tagName)
  )
    return;
  wakeControls();
  if (event.code === "Space") {
    event.preventDefault();
    $("play-toggle").click();
  } else if (model && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
    event.preventDefault();
    setPlaying(false);
    seek(
      time +
        (event.key === "ArrowRight" ? 1 : -1) / Math.max(1, model.nFrames - 1),
    );
  } else if (event.key.toLowerCase() === "r") fitCamera();
  else if (event.key.toLowerCase() === "f") $("fullscreen").click();
  else if (event.key.toLowerCase() === "o") $("file").click();
  else if (event.key.toLowerCase() === "u" && camera) {
    camera.upAxis = camera.upAxis === "y" ? "z" : "y";
    camera.fit();
    status(`${camera.upAxis.toUpperCase()} up`, "ready", 1500);
  }
});
window.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer.types.includes("Files")) {
    event.dataTransfer.dropEffect = "copy";
    $("drop-hint").hidden = false;
  }
});
window.addEventListener("dragleave", (event) => {
  if (!event.relatedTarget) $("drop-hint").hidden = true;
});
window.addEventListener("drop", (event) => {
  event.preventDefault();
  $("drop-hint").hidden = true;
  const file = event.dataTransfer.files[0];
  if (file) void load({ blob: file, name: file.name });
});
window.addEventListener("blur", () => {
  $("drop-hint").hidden = true;
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) setPlaying(false);
});
$("canvas").addEventListener("webglcontextlost", (event) => {
  event.preventDefault();
  loading = true;
  setPlaying(false);
  status("Graphics context lost. Reload the page to continue.", "error");
});
const resize = new ResizeObserver(() => {
  dirty = true;
});
resize.observe($("viewer"));
window.addEventListener("pagehide", (event) => {
  if (event.persisted) return;
  generation++;
  loadController?.abort();
  cancelAnimationFrame(raf);
  clearTimeout(idleTimer);
  clearTimeout(messageTimer);
  worker?.terminate();
  camera?.destroy();
  renderer?.destroy();
  resize.disconnect();
});
try {
  renderer = new SplatRenderer($("canvas"));
  camera = new OrbitCamera($("canvas"), () => {
    dirty = true;
  });
  camera.upAxis = query.get("up") === "z" ? "z" : "y";
  raf = requestAnimationFrame(tick);
  const url = query.get("src");
  if (url && query.has("view")) {
    bookmarkView = JSON.parse(query.get("view"));
    camera.restore(bookmarkView);
    bookmarkURL = new URL(url, location.href).href;
  }
  if (url) void load(url);
  else if (query.get("demo") === "1")
    void load({ blob: demoFile(), name: "Kinetic ribbon", demo: true });
} catch (error) {
  $("empty").hidden = true;
  status(error.message, "error");
}
