const protocol = "ftgs-player";
const version = 1;
const controllers = new WeakMap();

/** Parent-side controller for a same- or cross-origin player iframe. */
export class FTGSEmbed extends EventTarget {
  #iframe;
  #origin;
  #channel = Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) =>
    n.toString(16),
  ).join("-");
  #events = new AbortController();
  #pending = new Map();
  #id = 0;
  #state = null;
  #connected = false;
  #disposed = false;
  #destroyed = false;
  #timer;
  #retry;
  #resolveReady;
  #rejectReady;
  #requestTimeout;

  constructor(
    iframe,
    {
      src = iframe?.getAttribute("src"),
      timeout = 15000,
      requestTimeout = 120000,
    } = {},
  ) {
    super();
    if (!(iframe instanceof HTMLIFrameElement))
      throw new TypeError("FTGSEmbed needs an iframe element.");
    if (controllers.has(iframe))
      throw new Error("This iframe already has a controller.");
    if (!iframe.isConnected)
      throw new Error("Append the iframe before connecting it.");
    if (!src) throw new TypeError("Provide the player page URL as src.");
    const url = new URL(src, document.baseURI);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !/^https?:$/.test(location.protocol)
    )
      throw new TypeError("Serve the parent and player over HTTP or HTTPS.");
    if (![timeout, requestTimeout].every((n) => Number.isFinite(n) && n > 0))
      throw new RangeError("Timeouts must be positive milliseconds.");
    this.#iframe = iframe;
    this.#origin = url.origin;
    this.#requestTimeout = requestTimeout;
    this.ready = new Promise((resolve, reject) => {
      this.#resolveReady = resolve;
      this.#rejectReady = reject;
    });
    // Calls may await ready later, including after destroy during an early unmount.
    this.ready.catch(() => {});
    const options = { signal: this.#events.signal };
    window.addEventListener(
      "message",
      (event) => this.#receive(event),
      options,
    );
    iframe.addEventListener(
      "load",
      () => {
        if (this.#connected) {
          this.#dispose(
            new Error("The player iframe navigated. Create a new controller."),
          );
          return;
        }
        clearInterval(this.#retry);
        const connect = () => this.#post({ type: "connect" });
        connect();
        this.#retry = setInterval(connect, 150);
      },
      options,
    );
    this.#timer = setTimeout(
      () => this.#dispose(new Error("Player connection timed out.")),
      timeout,
    );
    controllers.set(iframe, this);
    url.searchParams.set("parentOrigin", location.origin);
    // Each controller starts a fresh page, including after an earlier destroy().
    iframe.src = url.href;
  }

  get state() {
    return this.#state ? { ...this.#state } : null;
  }
  #post(message) {
    this.#iframe.contentWindow?.postMessage(
      { protocol, version, channel: this.#channel, ...message },
      this.#origin,
    );
  }
  #receive(event) {
    const message = event.data;
    if (
      event.source !== this.#iframe.contentWindow ||
      event.origin !== this.#origin ||
      !message ||
      message.protocol !== protocol ||
      message.version !== version ||
      message.channel !== this.#channel
    )
      return;
    if (message.type === "connected" && !this.#connected) {
      this.#connected = true;
      this.#state = message.state;
      clearTimeout(this.#timer);
      clearInterval(this.#retry);
      this.#resolveReady(this.state);
      return;
    }
    if (!this.#connected) return;
    if (message.type === "event") {
      this.#state = message.state;
      this.dispatchEvent(
        new CustomEvent(message.event, { detail: this.state }),
      );
      if (message.event === "destroy")
        this.#dispose(new Error("The player has been destroyed."));
    } else if (message.type === "reply") {
      const request = this.#pending.get(message.id);
      if (!request) return;
      this.#pending.delete(message.id);
      clearTimeout(request.timer);
      this.#state = message.state;
      if (message.error) {
        const error = new Error(message.error.message);
        error.name = message.error.name;
        request.reject(error);
      } else request.resolve(this.state);
    }
  }

  async #request(method, args = []) {
    if (this.#disposed)
      throw new Error(
        "The iframe controller has been destroyed or disconnected.",
      );
    await this.ready;
    if (this.#disposed)
      throw new Error(
        "The iframe controller has been destroyed or disconnected.",
      );
    const id = ++this.#id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new Error(`Player command timed out: ${method}`));
      }, this.#requestTimeout);
      this.#pending.set(id, { resolve, reject, timer });
      try {
        this.#post({ type: "command", id, method, args });
      } catch (error) {
        clearTimeout(timer);
        this.#pending.delete(id);
        reject(error);
      }
    });
  }

  async load(source, options) {
    if (options?.signal)
      throw new TypeError("AbortSignal is supported by the canvas API only.");
    // Resolve paths in the host project, before crossing into the player's origin.
    if (typeof source === "string" || source instanceof URL)
      source = new URL(source, document.baseURI).href;
    return this.#request("load", [source, options]);
  }
  play() {
    return this.#request("play");
  }
  pause() {
    return this.#request("pause");
  }
  seek(time) {
    return this.#request("seek", [time]);
  }
  setView(view) {
    return this.#request("setView", [view]);
  }
  fitCamera() {
    return this.#request("fitCamera");
  }
  exitAR() {
    return this.#request("exitAR");
  }
  getState() {
    return this.#request("getState");
  }

  #dispose(error) {
    if (this.#disposed) return;
    this.#disposed = true;
    clearTimeout(this.#timer);
    clearInterval(this.#retry);
    this.#events.abort();
    this.#rejectReady(error);
    for (const request of this.#pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    this.#pending.clear();
    controllers.delete(this.#iframe);
  }

  /** Unload the child and release this controller. The iframe element is retained. */
  destroy() {
    if (this.#destroyed) return;
    this.#destroyed = true;
    const owner = controllers.get(this.#iframe);
    this.#dispose(
      new DOMException("The iframe controller was destroyed.", "AbortError"),
    );
    this.#state = {
      ...this.#state,
      status: "destroyed",
      loaded: false,
      playing: false,
    };
    if (!owner || owner === this) this.#iframe.src = "about:blank";
    this.dispatchEvent(new CustomEvent("destroy", { detail: this.state }));
  }
}
