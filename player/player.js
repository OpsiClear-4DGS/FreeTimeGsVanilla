import { readFTGS } from "./ftgs.js";
import { OrbitCamera, validateCameraView } from "./camera.js";
import { SplatRenderer } from "./renderer.js";

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

  constructor(
    canvas,
    {
      autoplay = true,
      loop = true,
      fps = 30,
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
    if (!Number.isFinite(fps) || fps <= 0)
      throw new RangeError("fps must be a positive number.");
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
      loop: Boolean(loop),
      fps,
      maxPoints,
      resolution,
      frames,
    };
    this.#canvas = canvas;
    try {
      this.#renderer = new SplatRenderer(canvas);
      this.#camera = new OrbitCamera(canvas, () => {
        this.#dirty = true;
        this.#revision++;
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
          if (document.hidden) this.pause();
          this.#previousTick = performance.now();
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
  get duration() {
    return this.#model
      ? Math.max(1, this.#model.nFrames - 1) / this.#options.fps
      : 0;
  }
  get state() {
    return {
      status: this.#status,
      loaded: this.#model !== null,
      playing: this.#playing,
      time: this.#time,
      currentTime: this.#time * this.duration,
      duration: this.duration,
      fps: this.#options.fps,
      loop: this.#options.loop,
      nFrames: this.#model?.nFrames ?? 0,
      name: this.#model?.name ?? "",
      pointCount: this.#model?.count ?? 0,
      sourceCount: this.#model?.sourceCount ?? 0,
      progress: this.#progress,
      error: this.#error,
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
    const controller = (this.#loadController = new AbortController());
    const cancel = () => controller.abort();
    signal?.addEventListener("abort", cancel, { once: true });
    this.#status = "loading";
    this.#error = null;
    this.#progress = 0;
    this.pause();
    this.#emit("loadstart");
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
      const data = await readFTGS(blob, {
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
      const worker = new Worker(new URL("./sort-worker.js", import.meta.url), {
        type: "module",
      });
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
        nFrames: this.#options.frames ?? data.nFrames ?? 300,
        count: data.count,
        sourceCount: data.sourceCount,
      };
      this.#view = view;
      this.#camera.fit(data.bounds);
      if (view) this.#camera.restore(view);
      this.#status = "ready";
      this.#progress = 1;
      this.#time = 0;
      this.#dirty = true;
      this.#revision++;
      this.#emit("loaded");
      // Event handlers may synchronously destroy the player or start another load.
      if (generation !== this.#generation) throw aborted();
      this.#emit("timeupdate");
      if (autoplay && !document.hidden) this.play();
      return this.state;
    } catch (error) {
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
      this.#model.nFrames === 1 ||
      !this.#worker
    )
      return this.state;
    if (this.#time === 1) this.seek(0);
    if (!this.#playing) {
      this.#playing = true;
      this.#previousTick = performance.now();
      this.#emit("play");
    }
    return this.state;
  }

  pause() {
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
    this.#emit("timeupdate");
    return this.state;
  }

  setView(view) {
    this.#assertAlive();
    this.#view = validateCameraView(view);
    this.#camera.restore(this.#view);
  }
  fitCamera() {
    this.#assertAlive();
    if (this.#view) this.#camera.restore(this.#view);
    else this.#camera.fit();
  }
  setUpAxis(up) {
    this.#assertAlive();
    if (!["y", "z"].includes(up)) throw new TypeError("up must be y or z.");
    this.#view = null;
    this.#camera.upAxis = up;
    this.#camera.fit();
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

  #tick = (now) => {
    const elapsed = Math.min((now - this.#previousTick) / 1000, 0.25);
    this.#previousTick = now;
    if (this.#model && this.#status !== "loading" && !this.#contextLost) {
      if (this.#playing) {
        const next = this.#time + elapsed / this.duration;
        this.#time = this.#options.loop ? next % 1 : Math.min(1, next);
        this.#dirty = true;
        if (!this.#options.loop && next >= 1) {
          this.pause();
          this.#emit("timeupdate");
          this.#emit("ended");
        } else if (now - this.#lastTimeEvent >= 100) {
          this.#lastTimeEvent = now;
          this.#emit("timeupdate");
        }
      }
      if (this.#dirty && !this.#pending && this.#worker) {
        this.#dirty = false;
        this.#pending = {
          time: this.#time,
          camera: this.#camera.snapshot(),
          revision: this.#revision,
        };
        this.#worker.postMessage({
          type: "sort",
          time: this.#time,
          view: this.#pending.camera.view,
          near: this.#pending.camera.near,
        });
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
    this.#playing = false;
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
