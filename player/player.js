import { readModel } from "./model.js?v=8";
import { OrbitCamera, validateCameraView } from "./camera.js?v=4";
import { SplatRenderer } from "./renderer.js?v=6";
import { ARPresentation, isARSupported } from "./xr.js?v=4";
import { AudioTrack } from "./audio.js?v=6";
import {
  resolvePlayback,
  validatePlaybackRate,
  validateVolume,
} from "./playback.js?v=8";

export const PLAYER_EVENTS = Object.freeze([
  "loadstart",
  "progress",
  "loaded",
  "play",
  "pause",
  "timeupdate",
  "ended",
  "abort",
  "error",
  "arstatechange",
  "audiochange",
  "ratechange",
  "destroy",
]);
const owners = new WeakMap();
const aborted = () => new DOMException("Loading was cancelled.", "AbortError");

/** A self-contained canvas player. No UI, global shortcuts, or document styles. */
export class FTGSPlayer extends EventTarget {
  #canvas;
  #renderer;
  #camera;
  #worker;
  #pending;
  #resize;
  #events = new AbortController();
  #loadController;
  #generation = 0;
  #revision = 0;
  #raf;
  #previousTick = performance.now();
  #lastTimeEvent = 0;
  #dirty = true;
  #model = null;
  #view = null;
  #time = 0;
  #playing = false;
  #status = "empty";
  #error = null;
  #progress = 0;
  #contextLost = false;
  #options;
  #ar = null;
  #arError = null;
  #xrOrder = null;
  #audio = null;
  #audioSuspended = false;

  static isARSupported() {
    return isARSupported();
  }

  constructor(
    canvas,
    {
      autoplay = true,
      loop = null,
      fps = null,
      playbackRate = null,
      volume = null,
      muted = false,
      maxPoints = 1000000,
      resolution = 1,
      frames = null,
      up = "y",
    } = {},
  ) {
    super();
    if (!(canvas instanceof HTMLCanvasElement))
      throw new TypeError("FTGSPlayer needs a canvas element.");
    if (owners.has(canvas))
      throw new Error("This canvas already has a player.");
    if (fps !== null && (!Number.isFinite(fps) || fps <= 0))
      throw new RangeError("fps must be a positive number.");
    if (playbackRate !== null) validatePlaybackRate(playbackRate);
    if (volume !== null) validateVolume(volume);
    if (loop !== null && typeof loop !== "boolean")
      throw new TypeError("loop must be a boolean or null.");
    if (typeof muted !== "boolean")
      throw new TypeError("muted must be a boolean.");
    if (
      maxPoints !== Infinity &&
      (!Number.isSafeInteger(maxPoints) || maxPoints < 1)
    )
      throw new RangeError("maxPoints must be a positive integer or Infinity.");
    if (!Number.isFinite(resolution) || resolution <= 0 || resolution > 1)
      throw new RangeError("resolution must be greater than 0 and at most 1.");
    if (frames !== null && (!Number.isSafeInteger(frames) || frames < 1))
      throw new RangeError("frames must be a positive integer.");
    if (!["y", "z"].includes(up)) throw new TypeError("up must be y or z.");
    this.#options = {
      autoplay: Boolean(autoplay),
      loop,
      fps,
      playbackRate,
      volume,
      muted,
      maxPoints,
      resolution,
      frames,
    };
    this.#canvas = canvas;
    try {
      this.#renderer = new SplatRenderer(canvas);
      this.#camera = new OrbitCamera(canvas, () => {
        this.#dirty = true;
        // Draw each completed sort with its own camera snapshot while moving.
        // Invalidating every camera step would starve drawing on slower workers.
      });
      this.#camera.upAxis = up;
      this.#resize = new ResizeObserver(() => {
        this.#dirty = true;
      });
      this.#resize.observe(canvas);
      const options = { signal: this.#events.signal };
      document.addEventListener(
        "visibilitychange",
        () => {
          if (!this.#ar) {
            if (document.hidden) this.pause();
            this.#previousTick = performance.now();
          }
        },
        options,
      );
      canvas.addEventListener(
        "webglcontextlost",
        (event) => {
          event.preventDefault();
          this.#contextLost = true;
          this.#generation++;
          this.#loadController?.abort();
          this.#worker?.terminate();
          this.#worker = null;
          this.#pending = null;
          this.#fail(
            new Error(
              "Graphics context lost. Recreate the player to continue.",
            ),
          );
        },
        options,
      );
      owners.set(canvas, this);
      this.#raf = requestAnimationFrame(this.#tick);
    } catch (error) {
      this.destroy();
      throw error;
    }
  }

  get time() {
    return this.#time;
  }
  get playing() {
    return this.#playing;
  }
  get fps() {
    return this.#options.fps ?? this.#model?.fps ?? 30;
  }
  get playbackRate() {
    return this.#options.playbackRate ?? this.#model?.playbackRate ?? 1;
  }
  get loop() {
    return this.#options.loop ?? this.#model?.loop ?? true;
  }
  get frameIndex() {
    if (!this.#model) return 0;
    const { nFrames, timelineMode } = this.#model;
    return timelineMode === 1
      ? Math.min(nFrames - 1, Math.floor(this.#time * nFrames))
      : Math.round(this.#time * (nFrames - 1));
  }
  get duration() {
    return this.#model?.duration ?? 0;
  }
  get state() {
    return {
      status: this.#status,
      loaded: this.#model !== null,
      playing: this.#playing,
      time: this.#time,
      currentTime: this.#time * this.duration,
      duration: this.duration,
      fps: this.fps,
      loop: this.loop,
      playbackRate: this.playbackRate,
      playable: this.#model?.playable ?? false,
      nFrames: this.#model?.nFrames ?? 0,
      frameIndex: this.frameIndex,
      format: this.#model?.format ?? null,
      timeline: this.#model
        ? ["continuous", "discrete", "static"][this.#model.timelineMode]
        : null,
      name: this.#model?.name ?? "",
      pointCount: this.#model?.count ?? 0,
      sourceCount: this.#model?.sourceCount ?? 0,
      progress: this.#progress,
      error: this.#error,
      audio: {
        available: Boolean(this.#audio),
        muted: this.#options.muted,
        volume: this.#options.volume ?? this.#model?.volume ?? 1,
        ...(this.#audio?.state ?? {
          status: "none",
          currentTime: 0,
          duration: null,
          error: null,
        }),
      },
      ar: {
        status: this.#ar
          ? this.#ar.active
            ? "presenting"
            : "starting"
          : "inactive",
        placed: this.#ar?.placed ?? false,
        hitTest: Boolean(this.#ar?.hitSource),
        surface: this.#ar?.surface ?? false,
        error: this.#arError,
      },
    };
  }

  #assertAlive() {
    if (this.#status === "destroyed")
      throw new Error("The player has been destroyed.");
    if (this.#contextLost)
      throw new Error(
        "Graphics context lost. Recreate the player to continue.",
      );
  }
  #emit(type) {
    this.dispatchEvent(new CustomEvent(type, { detail: this.state }));
  }
  #fail(error) {
    void this.exitAR();
    this.#status = "error";
    this.#error = error.message;
    this.pause();
    this.#emit("error");
  }

  /** Load a File/Blob or HTTP(S) URL. Superseded loads reject with AbortError. */
  async load(
    input,
    { name, view = null, autoplay = this.#options.autoplay, signal } = {},
  ) {
    this.#assertAlive();
    signal?.throwIfAborted();
    const generation = ++this.#generation;
    this.#loadController?.abort();
    this.#arError = null;
    void this.exitAR();
    this.#camera.stopMoving();
    const controller = (this.#loadController = new AbortController());
    const cancel = () => controller.abort();
    signal?.addEventListener("abort", cancel, { once: true });
    this.#status = "loading";
    this.#error = null;
    this.#progress = 0;
    this.pause();
    this.#emit("loadstart");
    let pendingAudio;
    try {
      let blob = input;
      if (typeof input === "string" || input instanceof URL) {
        const url = new URL(input, document.baseURI);
        if (!["http:", "https:"].includes(url.protocol))
          throw new TypeError("Use an HTTP or HTTPS model URL.");
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok)
          throw new Error(`Model request failed: HTTP ${response.status}.`);
        blob = await response.blob();
        if (name === undefined) {
          name = url.pathname.split("/").pop() || "Remote model";
          try {
            name = decodeURIComponent(name);
          } catch {
            /* Keep an undecodable name. */
          }
        }
      }
      if (!(blob instanceof Blob))
        throw new TypeError("load() needs a File, Blob, or HTTP(S) URL.");
      if (name !== undefined && typeof name !== "string")
        throw new TypeError("name must be a string.");
      // Validate a saved view before replacing a working model or camera.
      if (view !== null) view = validateCameraView(view);
      const data = await readModel(blob, {
        maxPoints: this.#options.maxPoints,
        signal: controller.signal,
        onProgress: (progress) => {
          if (generation !== this.#generation) return;
          this.#progress = progress;
          this.#emit("progress");
        },
      });
      controller.signal.throwIfAborted();
      if (generation !== this.#generation) throw aborted();
      const playback = resolvePlayback(data, this.#options);
      if (data.audio) {
        const track = new AudioTrack(data.audio.blob, () => {
          if (this.#audio === track) this.#emit("audiochange");
        });
        pendingAudio = track;
      }
      const worker = new Worker(
        new URL("./sort-worker.js?v=6", import.meta.url),
        {
          type: "module",
        },
      );
      try {
        this.#renderer.setModel(data);
        this.#connectWorker(worker, data);
      } catch (error) {
        worker.terminate();
        throw error;
      }
      this.#model = {
        name: name ?? blob.name ?? "FTGS model",
        degree: data.degree,
        ...playback,
        volume: data.audio?.volume ?? 1,
        format: data.format,
        timelineMode: data.timelineMode,
        count: data.count,
        sourceCount: data.sourceCount,
      };
      this.#audio?.destroy();
      this.#audio = pendingAudio ?? null;
      pendingAudio = null;
      this.#audioSuspended = false;
      this.#view = view;
      this.#camera.fit(data.bounds);
      if (view) this.#camera.restore(view);
      this.#status = "ready";
      this.#progress = 1;
      this.#time = 0;
      this.#syncAudio(true);
      this.#dirty = true;
      this.#revision++;
      this.#emit("loaded");
      // Event handlers may synchronously destroy the player or start another load.
      if (generation !== this.#generation) throw aborted();
      this.#emit("timeupdate");
      if (autoplay && !document.hidden) this.play();
      return this.state;
    } catch (error) {
      pendingAudio?.destroy();
      if (generation !== this.#generation || this.#status === "destroyed")
        throw aborted();
      if (controller.signal.aborted) {
        this.#status = this.#model ? "ready" : "empty";
        this.#emit("abort");
        throw aborted();
      }
      this.#dirty = true;
      this.#fail(error);
      throw error;
    } finally {
      signal?.removeEventListener("abort", cancel);
      if (generation === this.#generation) this.#loadController = null;
    }
  }

  play() {
    this.#assertAlive();
    if (
      !this.#model ||
      this.#status === "loading" ||
      !this.#model.playable ||
      !this.#worker
    )
      return this.state;
    if (this.#time === 1) this.seek(0);
    this.#audio?.retry();
    if (!this.#playing) {
      this.#playing = true;
      this.#previousTick = performance.now();
      this.#emit("play");
    }
    this.#syncAudio(true);
    return this.state;
  }

  pause() {
    this.#audio?.pause();
    if (this.#playing) {
      this.#playing = false;
      this.#emit("pause");
    }
    return this.state;
  }

  /** Seek in normalized model time [0, 1], preserving the playing/paused state. */
  seek(time) {
    this.#assertAlive();
    if (!Number.isFinite(time))
      throw new TypeError("seek() needs a finite normalized time.");
    if (!this.#model) return this.state;
    this.#time = Math.max(0, Math.min(1, time));
    this.#previousTick = performance.now();
    this.#dirty = true;
    this.#revision++;
    this.#xrOrder = null;
    this.#syncAudio(true);
    this.#emit("timeupdate");
    return this.state;
  }

  #syncAudio(seek = false) {
    this.#audio?.sync(
      this.#time * this.duration,
      {
        playing: this.#playing && !this.#audioSuspended,
        playbackRate: this.playbackRate,
        muted: this.#options.muted,
        volume: this.#options.volume ?? this.#model?.volume ?? 1,
      },
      seek,
    );
  }
  setPlaybackRate(rate) {
    this.#assertAlive();
    this.#options.playbackRate = validatePlaybackRate(rate);
    this.#previousTick = performance.now();
    this.#syncAudio(true);
    this.#emit("ratechange");
    return this.state;
  }
  setMuted(muted) {
    this.#assertAlive();
    if (typeof muted !== "boolean")
      throw new TypeError("muted must be a boolean.");
    this.#options.muted = muted;
    this.#audio?.retry();
    this.#syncAudio(true);
    this.#emit("audiochange");
    return this.state;
  }
  setVolume(volume) {
    this.#assertAlive();
    this.#options.volume = validateVolume(volume);
    this.#syncAudio();
    this.#emit("audiochange");
    return this.state;
  }

  setView(view) {
    this.#assertAlive();
    this.#view = validateCameraView(view);
    this.#camera.restore(this.#view);
  }
  fitCamera() {
    this.#assertAlive();
    if (this.#ar?.active) {
      this.#ar.reset();
      return;
    }
    if (this.#view) this.#camera.restore(this.#view);
    else this.#camera.fit();
  }
  setUpAxis(up) {
    this.#assertAlive();
    if (!["y", "z"].includes(up)) throw new TypeError("up must be y or z.");
    this.#view = null;
    this.#camera.upAxis = up;
    this.#camera.fit();
    if (this.#ar) {
      this.#ar.up = up;
      this.#ar.reset();
    }
  }

  /** Call directly from a click/tap handler. Size is the scene's diameter in meters. */
  async enterAR({ overlayRoot = null, size = 1 } = {}) {
    this.#assertAlive();
    if (!this.#model || this.#status === "loading" || !this.#worker)
      throw new Error("Load a model before entering AR.");
    if (this.#ar) throw new Error("AR is already starting or active.");
    if (!Number.isFinite(size) || size <= 0)
      throw new RangeError("AR size must be a positive number of meters.");
    if (overlayRoot !== null && !(overlayRoot instanceof Element))
      throw new TypeError("overlayRoot must be a DOM element.");
    this.#arError = null;
    this.#camera.stopMoving();
    this.#camera.enabled = false;
    this.#audioSuspended = true;
    this.#audio?.pause();
    this.#revision++;
    this.#xrOrder = null;
    const ar = new ARPresentation(this.#renderer, {
      bounds: this.#camera.bounds,
      up: this.#camera.upAxis,
      size,
      resolution: this.#options.resolution,
      onFrame: this.#xrFrame,
      onChange: () => this.#emit("arstatechange"),
      onError: (error) => {
        this.#arError = error.message;
        this.#emit("arstatechange");
      },
      onEnd: () => {
        if (this.#ar !== ar) return;
        this.#ar = null;
        this.#camera.enabled = true;
        this.#camera.stopMoving();
        this.#xrOrder = null;
        this.#revision++;
        this.#dirty = true;
        this.#previousTick = performance.now();
        this.#audioSuspended = false;
        this.#syncAudio(true);
        if (this.#status !== "destroyed") this.#emit("arstatechange");
      },
    });
    this.#ar = ar;
    this.#emit("arstatechange");
    try {
      await ar.start(overlayRoot);
      if (this.#ar !== ar)
        throw new DOMException("AR was cancelled.", "AbortError");
      this.#previousTick = performance.now();
      this.#emit("arstatechange");
      return this.state;
    } catch (error) {
      await ar.stop();
      if (error.name !== "AbortError" && this.#status !== "destroyed") {
        this.#arError = error.message;
        this.#emit("arstatechange");
      }
      throw error;
    }
  }

  async exitAR() {
    await this.#ar?.stop();
    return this.state;
  }

  #connectWorker(worker, data) {
    worker.postMessage(
      {
        type: "model",
        model: {
          positionTime: data.positionTime,
          velocityDuration: data.velocityDuration,
          alpha: data.alpha,
          useVelocity: data.useVelocity,
          opacityFloor: data.opacityFloor,
          timelineMode: data.timelineMode,
        },
      },
      [
        data.positionTime.buffer,
        data.velocityDuration.buffer,
        data.alpha.buffer,
      ],
    );
    this.#worker?.terminate();
    this.#worker = worker;
    this.#pending = null;
    const fail = (error) => {
      if (this.#worker !== worker) return;
      worker.terminate();
      this.#worker = null;
      this.#pending = null;
      this.#fail(error);
    };
    worker.onmessage = ({ data: result }) => {
      if (this.#worker !== worker) return;
      const request = this.#pending;
      this.#pending = null;
      if (result.type === "error") return fail(new Error(result.message));
      if (
        !request ||
        request.revision !== this.#revision ||
        this.#status === "loading"
      )
        return;
      try {
        if (request.xr) {
          if (this.#ar?.active)
            this.#xrOrder = { order: result.order, time: request.time };
        } else if (!this.#ar)
          this.#renderer.draw(
            result.order,
            request.camera,
            request.time,
            this.#model.degree,
            this.#options.resolution,
          );
      } catch (error) {
        fail(error);
      }
    };
    worker.onerror = (event) =>
      fail(new Error(`Render worker failed: ${event.message}`));
  }

  #advance(now) {
    const elapsed = Math.max(
      0,
      Math.min((now - this.#previousTick) / 1000, 0.25),
    );
    this.#previousTick = now;
    if (this.#playing) {
      const next = this.#time + (elapsed * this.playbackRate) / this.duration;
      this.#time = this.loop ? next % 1 : Math.min(1, next);
      this.#dirty = true;
      this.#syncAudio(next >= 1);
      if (!this.loop && next >= 1) {
        this.pause();
        this.#emit("timeupdate");
        this.#emit("ended");
      } else if (now - this.#lastTimeEvent >= 100) {
        this.#lastTimeEvent = now;
        this.#emit("timeupdate");
      }
    }
    return elapsed;
  }

  #sort(camera, xr = false) {
    if (this.#pending || !this.#worker) return;
    this.#dirty = false;
    // Select discrete frames once, on the CPU, so shader and sorter agree at boundaries.
    const time = this.#model.timelineMode === 1 ? this.frameIndex : this.#time;
    this.#pending = { time, camera, revision: this.#revision, xr };
    this.#worker.postMessage({
      type: "sort",
      time,
      view: camera.view,
      // Retain behind-camera centers in XR: a newer tracked pose can reveal them
      // before the next asynchronous sort. Each eye clips its own near plane.
      near: xr ? -Infinity : camera.near,
    });
  }

  #xrFrame = (now, frame) => {
    if (!frame || !this.#model || !this.#ar?.active) {
      this.#previousTick = now;
      this.#audioSuspended = true;
      this.#audio?.pause();
      return;
    }
    this.#audioSuspended = false;
    this.#advance(now);
    if (!this.#ar?.active) return; // A playback event may unload or destroy us.
    this.#sort(frame.camera, true);
    if (!this.#xrOrder) return;
    // Always draw from the current tracked pose, even while a worker is sorting.
    // Reuse the most recent time-consistent order for both eyes.
    for (const { camera, viewport } of frame.views)
      this.#renderer.draw(
        this.#xrOrder.order,
        camera,
        this.#xrOrder.time,
        this.#model.degree,
        this.#options.resolution,
        viewport,
      );
  };

  #tick = (now) => {
    if (!this.#ar) {
      const elapsed = this.#advance(now);
      if (this.#model && this.#status !== "loading" && !this.#contextLost) {
        this.#camera.update(elapsed);
        if (this.#dirty) this.#sort(this.#camera.snapshot());
      }
    }
    if (this.#status !== "destroyed")
      this.#raf = requestAnimationFrame(this.#tick);
  };

  /** Release downloads, worker, listeners, animation, observer, and GPU resources. */
  destroy() {
    if (this.#status === "destroyed") return;
    this.#generation++;
    this.#loadController?.abort();
    this.#status = "destroyed";
    void this.exitAR();
    this.#playing = false;
    this.#audio?.destroy();
    this.#audio = null;
    cancelAnimationFrame(this.#raf);
    this.#events.abort();
    this.#resize?.disconnect();
    this.#worker?.terminate();
    this.#worker = null;
    this.#pending = null;
    this.#camera?.destroy();
    this.#renderer?.destroy();
    this.#model = null;
    this.#view = null;
    this.#time = 0;
    owners.delete(this.#canvas);
    this.#emit("destroy");
  }
}
