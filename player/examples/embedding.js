import { FTGSPlayer, FTGSEmbed } from "../index.js";
import { demoFile } from "../site/demo.js";
import { readExample, downloadExample } from "../site/examples.js?v=2";

const $ = (id) => document.getElementById(id);
const query = new URLSearchParams(location.search);
const iframe = query.get("mode") === "iframe";
const nativeUI = iframe && query.get("ui") === "player";
const exampleId = query.get("example");
if (nativeUI) $("controls").hidden = true;
$("description").textContent = iframe
  ? "A hosted player with controls on this page."
  : "A canvas player with its own playback controls.";
$("jensen-example").href = `?mode=${iframe ? "iframe" : "canvas"}&example=jensen-2m`;
for (const mode of ["canvas", "iframe"]) {
  const link = $(`${mode}-example`);
  const params = new URLSearchParams({ mode });
  if (exampleId) params.set("example", exampleId);
  link.href = `?${params}`;
  if ((iframe ? "iframe" : "canvas") === mode) link.setAttribute("aria-current", "page");
}

let player;
const download = new AbortController();
addEventListener("pagehide", (event) => {
  if (!event.persisted) { download.abort(); player?.destroy(); }
});
function update(state) {
  $("viewer").dataset.state = state.status;
  if (state.loaded) $("viewer").dataset.loaded = "true";
  $("play-toggle").disabled = !state.playable;
  const label = state.playing ? "Pause" : "Play";
  $("play-toggle").setAttribute("aria-label", label);
  $("play-toggle").title = label;
  $("play-icon").setAttribute("href", state.playing ? "#pause" : "#play");
  $("timeline").disabled = !state.playable;
  $("timeline").value = state.time;
  $("timeline").style.setProperty("--progress", `${state.time * 100}%`);
  const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  $("timecode").textContent = `${clock(state.currentTime)} / ${clock(state.duration)}`;
  const rate = $("playback-rate");
  if (![...rate.options].some((option) => Number(option.value) === state.playbackRate))
    rate.add(new Option(`${state.playbackRate}×`, String(state.playbackRate)));
  rate.value = state.playbackRate;
  rate.disabled = !state.playable || state.status === "loading";
  $("message").textContent = state.error ?? "";
  $("message").dataset.state = state.status;
  const silent = state.audio.muted || state.audio.volume === 0 || state.audio.status === "blocked";
  $("audio-controls").hidden = !state.audio.available;
  $("mute-toggle").disabled = state.audio.status === "error";
  $("mute-toggle").setAttribute("aria-label", silent ? "Unmute" : "Mute");
  $("mute-toggle").title = silent ? "Unmute" : "Mute";
  $("mute-toggle").setAttribute("aria-pressed", String(state.audio.muted));
  $("audio-icon").setAttribute("href", silent ? "#silent" : "#sound");
}
function updateFullscreen() {
  const full = document.fullscreenElement === $("player-stage");
  $("fullscreen").setAttribute("aria-label", full ? "Exit fullscreen" : "Fullscreen");
  $("fullscreen").title = full ? "Exit fullscreen" : "Fullscreen";
  $("fullscreen-icon").setAttribute("href", full ? "#collapse" : "#expand");
}
document.addEventListener("fullscreenchange", updateFullscreen, { signal: download.signal });
$("fullscreen").onclick = async () => {
  try {
    if (document.fullscreenElement === $("player-stage")) await document.exitFullscreen();
    else await $("player-stage").requestFullscreen();
  } catch (error) { $("message").textContent = error.message; }
};

try {
  if (iframe) {
    const frame = document.createElement("iframe");
    frame.title = "Embedded 4D scene";
    frame.allow = "autoplay; xr-spatial-tracking";
    frame.allowFullscreen = true;
    $("viewer").replaceChildren(frame);
    const src = new URL(`../?autoplay=0&controls=${nativeUI ? "1" : "0"}`, import.meta.url);
    if (exampleId) src.searchParams.set("points", "all");
    player = new FTGSEmbed(frame, { src });
    await player.ready;
  } else {
    player = new FTGSPlayer($("canvas"), {
      autoplay: false,
      maxPoints: exampleId ? Infinity : 1000000,
    });
  }
  for (const type of ["loaded", "play", "pause", "timeupdate", "ratechange", "error"])
    player.addEventListener(type, ({ detail }) => update(detail));
  player.addEventListener("audiochange", ({ detail }) => {
    if (detail.audio.status === "blocked" && !detail.audio.muted && detail.playing)
      void Promise.resolve(player.pause()).then(update).catch((error) => { $("message").textContent = error.message; });
    else update(detail);
  });
  $("play-toggle").onclick = async () => {
    try { update(await (player.state.playing ? player.pause() : player.play())); }
    catch (error) { $("message").textContent = error.message; }
  };
  $("timeline").oninput = async () => {
    try {
      const time = Number($("timeline").value);
      await player.pause();
      update(await player.seek(time));
    } catch (error) { $("message").textContent = error.message; }
  };
  $("playback-rate").onchange = async () => {
    try { update(await player.setPlaybackRate(Number($("playback-rate").value))); }
    catch (error) { $("message").textContent = error.message; }
  };
  $("mute-toggle").onclick = async () => {
    try {
      const audio = player.state.audio;
      if (audio.volume === 0) await player.setVolume(1);
      update(await player.setMuted(audio.status === "blocked" || audio.volume === 0 ? false : !audio.muted));
      if (audio.status === "blocked") update(await player.play());
    } catch (error) { $("message").textContent = error.message; }
  };
  if (exampleId) {
    $("message").textContent = "Downloading example…";
    $("message").dataset.state = "loading";
    const example = await readExample(exampleId, { signal: download.signal });
    const blob = await downloadExample(example, {
      signal: download.signal,
      onProgress: (progress) => { $("message").textContent = `Downloading · ${Math.round(progress * 100)}%`; },
    });
    update(await player.load(blob, { name: example.title, view: example.view }));
  } else update(await player.load(demoFile(), { name: "Generated demo" }));
} catch (error) {
  if (error.name !== "AbortError") $("message").textContent = error.message;
}
