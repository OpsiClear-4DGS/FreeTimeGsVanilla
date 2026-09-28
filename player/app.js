import { readFTGS } from "./ftgs.js";
import { OrbitCamera } from "./camera.js";
import { SplatRenderer } from "./renderer.js";
import { demoFile } from "./demo.js";

const $ = (id) => document.getElementById(id);
const number = (value) => value.toLocaleString();
const clock = (seconds) => {
  const hundredths = Math.round(seconds * 100);
  return `${Math.floor(hundredths / 6000)
    .toString()
    .padStart(
      2,
      "0",
    )}:${((hundredths % 6000) / 100).toFixed(2).padStart(5, "0")}`;
};
let renderer, camera, worker, pending, model, source, loadController;
let bookmarkView, bookmarkURL;
let generation = 0,
  time = 0,
  playing = false,
  loop = true,
  loading = false,
  dirty = true,
  raf;
let previousTick = performance.now(),
  lastDraw = 0,
  smoothFPS = 0;

function status(text, state = "ready") {
  $("message-text").textContent = text;
  $("message").dataset.state = state;
}
function frameCount() {
  return Math.max(1, Number($("frames").value) || 1);
}
function duration() {
  return Math.max(1, frameCount() - 1) / Number($("fps").value);
}
function updateTime() {
  $("timeline").value = time;
  $("timeline").style.setProperty("--progress", `${time * 100}%`);
  $("timeline").setAttribute(
    "aria-valuetext",
    `Frame ${Math.round(time * (frameCount() - 1)) + 1} of ${frameCount()}`,
  );
  $("timecode").replaceChildren(
    document.createTextNode(clock(time * duration())),
    Object.assign(document.createElement("span"), {
      textContent: `/ ${clock(duration())}`,
    }),
  );
}
function setPlaying(value, announce = true) {
  playing = Boolean(value && model && frameCount() > 1 && !loading);
  $("play-toggle").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("play-icon").setAttribute("href", playing ? "#pause" : "#play");
  previousTick = performance.now();
  if (model && announce) status(playing ? "Playing" : "Paused");
}
function seek(value) {
  time = Math.max(0, Math.min(1, value));
  dirty = true;
  updateTime();
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
      setPlaying(false, false);
      status(result.message, "error");
      return;
    }
    if (!request || loading) return;
    try {
      renderer.draw(
        result.order,
        request.camera,
        request.time,
        Number($("sh-degree").value),
        Number($("resolution").value),
      );
      $("visible-count").textContent = number(result.order.length);
      const now = performance.now();
      if (lastDraw && now - lastDraw < 2000) {
        const fps = 1000 / (now - lastDraw);
        smoothFPS = smoothFPS ? smoothFPS * 0.85 + fps * 0.15 : fps;
        $("render-fps").textContent = `${Math.round(smoothFPS)} fps`;
      }
      lastDraw = now;
    } catch (error) {
      setPlaying(false, false);
      status(error.message, "error");
    }
  };
  active.onerror = (event) => {
    pending = null;
    setPlaying(false, false);
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
  setPlaying(false, false);
  status(
    typeof input === "string" ? "Downloading model…" : "Reading model…",
    "loading",
  );
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
      maxPoints: Number($("point-limit").value),
      signal,
      onProgress: (progress) => {
        if (id === generation)
          status(`Reading model · ${Math.round(progress * 100)}%`, "loading");
      },
    });
    if (id !== generation) return;
    status("Preparing scene…", "loading");
    renderer.setModel(data);
    model = {
      count: data.count,
      sourceCount: data.sourceCount,
      degree: data.degree,
      nFrames: data.nFrames,
    };
    source = item;
    camera.fit(data.bounds);
    if (bookmarkView && item.url === bookmarkURL) {
      camera.restore(bookmarkView);
      $("up-axis").value = camera.upAxis;
    }
    connectWorker(data);
    $("scene-name").textContent = item.name;
    $("point-count").textContent = number(data.count);
    $("file-size").textContent =
      `${(item.blob.size / 1024 / 1024).toFixed(1)} MB`;
    $("motion").textContent = data.useVelocity
      ? "Linear velocity"
      : "Static positions";
    $("frames").value = data.nFrames ?? 300;
    $("frame-note").textContent = data.nFrames
      ? "Frame count from file. Playback rate is adjustable."
      : "Frame count is absent. Set Frames and Frame rate for playback timing.";
    $("preview-note").textContent =
      data.count < data.sourceCount
        ? `Preview: showing ${number(data.count)} of ${number(data.sourceCount)} Gaussians. Choose All points for full detail.`
        : "All Gaussians loaded.";
    $("sh-degree").replaceChildren(
      ...Array.from(
        { length: data.degree + 1 },
        (_, i) => new Option(String(i), String(i), false, i === data.degree),
      ),
    );
    $("demo-label").hidden = !item.demo;
    for (const name of ["play-toggle", "timeline", "restart"])
      $(name).disabled = false;
    loading = false;
    lastDraw = 0;
    smoothFPS = 0;
    seek(0);
    setPlaying(true);
    dirty = true;
  } catch (error) {
    if (id !== generation || error.name === "AbortError") return;
    loading = false;
    dirty = true;
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
      time += (elapsed / duration()) * Number($("speed").value);
      if (time >= 1) {
        if (loop) time %= 1;
        else {
          time = 1;
          setPlaying(false);
        }
      }
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

$("open-file").onclick = () => $("file").click();
$("file").onchange = () => {
  const file = $("file").files[0];
  if (file) void load({ blob: file, name: file.name });
  $("file").value = "";
};
$("demo").onclick = () =>
  void load({ blob: demoFile(), name: "Kinetic ribbon", demo: true });
$("url-open").onclick = () => $("url-dialog").showModal();
$("url-close").onclick = () => $("url-dialog").close();
$("url-form").onsubmit = (event) => {
  event.preventDefault();
  $("url-dialog").close();
  void load($("model-url").value.trim());
};
$("play-toggle").onclick = () => {
  if (!playing && time === 1) seek(0);
  setPlaying(!playing);
};
$("restart").onclick = () => seek(0);
$("timeline").oninput = () => {
  setPlaying(false);
  seek(Number($("timeline").value));
};
$("loop-toggle").onclick = () => {
  loop = !loop;
  $("loop-toggle").classList.toggle("active", loop);
  $("loop-toggle").setAttribute("aria-pressed", String(loop));
};
$("fit-camera").onclick = () => {
  if (bookmarkView && source?.url === bookmarkURL) {
    camera.restore(bookmarkView);
    $("up-axis").value = camera.upAxis;
  } else camera?.fit();
};
$("fullscreen").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await $("viewer").requestFullscreen();
  } catch {
    status("Fullscreen is unavailable in this browser.", "error");
  }
};
$("details-toggle").onclick = () => {
  $("details").hidden = !$("details").hidden;
  $("details-toggle").setAttribute(
    "aria-expanded",
    String(!$("details").hidden),
  );
};
$("point-limit").onchange = () => {
  if (source) void load(source);
};
for (const name of ["sh-degree", "resolution"])
  $(name).onchange = () => {
    dirty = true;
  };
$("up-axis").onchange = () => {
  if (camera) {
    camera.upAxis = $("up-axis").value;
    camera.fit();
  }
};
for (const name of ["fps", "frames"])
  $(name).onchange = () => {
    if (name === "frames")
      $("frames").value = Math.max(
        1,
        Math.min(1000000, Math.round(frameCount())),
      );
    if (frameCount() === 1) {
      seek(0);
      setPlaying(false);
    }
    updateTime();
  };
window.addEventListener("keydown", (event) => {
  if (
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    $("url-dialog").open ||
    ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(event.target.tagName)
  )
    return;
  if (event.code === "Space") {
    event.preventDefault();
    $("play-toggle").click();
  } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    setPlaying(false);
    seek(
      time +
        (event.key === "ArrowRight" ? 1 : -1) / Math.max(1, frameCount() - 1),
    );
  } else if (event.key.toLowerCase() === "r") $("fit-camera").click();
  else if (event.key.toLowerCase() === "f") $("fullscreen").click();
});
window.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer.types.includes("Files")) $("drop-hint").hidden = false;
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
document.addEventListener("visibilitychange", () => {
  if (document.hidden) setPlaying(false);
});
$("canvas").addEventListener("webglcontextlost", (event) => {
  event.preventDefault();
  loading = true;
  setPlaying(false, false);
  status(
    "Graphics context lost. Refresh the page and choose a smaller point limit.",
    "error",
  );
});
if (matchMedia("(max-width: 720px)").matches) {
  $("details").hidden = true;
  $("details-toggle").setAttribute("aria-expanded", "false");
}
const resize = new ResizeObserver(() => {
  dirty = true;
});
resize.observe($("viewer"));
window.addEventListener("pagehide", (event) => {
  if (event.persisted) return;
  generation++;
  loadController?.abort();
  cancelAnimationFrame(raf);
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
  raf = requestAnimationFrame(tick);
  const query = new URLSearchParams(location.search);
  const url = query.get("src");
  if (["24", "30", "60"].includes(query.get("fps")))
    $("fps").value = query.get("fps");
  if (url && query.has("view")) {
    bookmarkView = JSON.parse(query.get("view"));
    camera.restore(bookmarkView);
    bookmarkURL = new URL(url, location.href).href;
  }
  void load(url || { blob: demoFile(), name: "Kinetic ribbon", demo: true });
} catch (error) {
  status(error.message, "error");
}
