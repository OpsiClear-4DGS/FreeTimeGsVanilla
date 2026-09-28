/** One embedded audio track, following the player's scene clock. */
export class AudioTrack {
  #element;
  #url;
  #events = new AbortController();
  #notify;
  #status = "ready";
  #error = null;
  #attempted = false;
  #request = 0;
  #time = 0;

  constructor(blob, onChange) {
    this.#notify = onChange;
    this.#element = new Audio();
    const audio = this.#element;
    audio.preload = "auto";
    const options = { signal: this.#events.signal };
    audio.addEventListener(
      "loadedmetadata",
      () => {
        this.#seek(this.#time);
        this.#notify();
      },
      options,
    );
    audio.addEventListener(
      "ended",
      () => {
        if (audio.ended) this.#change("ended");
      },
      options,
    );
    audio.addEventListener(
      "error",
      () => {
        this.#error =
          "The embedded audio could not be decoded by this browser.";
        this.#change("error");
      },
      options,
    );
    audio.src = this.#url = URL.createObjectURL(blob);
  }

  get state() {
    const audio = this.#element;
    return {
      status: this.#status,
      currentTime: audio.currentTime,
      duration: Number.isFinite(audio.duration) ? audio.duration : null,
      error: this.#error,
    };
  }
  #change(status) {
    if (this.#events.signal.aborted || this.#status === status) return;
    this.#status = status;
    this.#notify();
  }
  #seek(time) {
    this.#time = time;
    if (this.#element.readyState > 0) {
      if (
        (this.#element.ended || this.#status === "ended") &&
        time < this.#element.duration
      )
        this.#attempted = false;
      this.#element.currentTime = Math.min(time, this.#element.duration);
    }
  }

  sync(time, { playing, playbackRate, muted, volume }, seek = false) {
    if (this.#events.signal.aborted) return;
    const audio = this.#element;
    if (audio.muted !== muted) audio.muted = muted;
    if (audio.volume !== volume) audio.volume = volume;
    if (audio.playbackRate !== playbackRate) audio.playbackRate = playbackRate;
    this.#time = time;
    // Correct stalls, loop boundaries and seeks without re-seeking every frame.
    if (
      seek ||
      (this.#status === "playing" && Math.abs(audio.currentTime - time) > 0.1)
    )
      this.#seek(time);
    if (
      !playing ||
      (Number.isFinite(audio.duration) && time >= audio.duration)
    ) {
      this.pause();
      return;
    }
    if (this.#attempted || this.#status === "error") return;
    this.#attempted = true;
    const request = ++this.#request;
    this.#change("ready");
    audio
      .play()
      .then(() => {
        if (request === this.#request && !this.#events.signal.aborted)
          this.#change("playing");
      })
      .catch((error) => {
        if (request !== this.#request || this.#events.signal.aborted) return;
        if (error.name === "NotAllowedError") this.#change("blocked");
        else if (error.name !== "AbortError") {
          this.#error =
            "The embedded audio could not be played by this browser.";
          this.#change("error");
        }
      });
  }

  retry() {
    this.#attempted = false;
  }
  pause() {
    this.#request++;
    this.#attempted = false;
    this.#element.pause();
    if (this.#status === "playing") this.#change("paused");
  }
  destroy() {
    this.#events.abort();
    this.pause();
    this.#element.removeAttribute("src");
    this.#element.load();
    URL.revokeObjectURL(this.#url);
  }
}
