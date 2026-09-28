import { PLAYER_EVENTS } from "./player.js?v=6";

const protocol = "ftgs-player";
const version = 1;

/** Opt-in bridge for the standalone page. Only its configured parent can control it. */
export function attachPlayerBridge(player, parentOrigin) {
  if (!parentOrigin || window.parent === window) return () => {};
  const origin = new URL(parentOrigin);
  if (
    !["http:", "https:"].includes(origin.protocol) ||
    origin.origin !== parentOrigin
  )
    throw new TypeError("parentOrigin must be an exact HTTP(S) origin.");
  const events = new AbortController();
  let channel;
  const send = (message) =>
    window.parent.postMessage(
      { protocol, version, channel, ...message },
      parentOrigin,
    );
  window.addEventListener(
    "message",
    async (event) => {
      const message = event.data;
      if (
        event.source !== window.parent ||
        event.origin !== parentOrigin ||
        !message ||
        message.protocol !== protocol ||
        message.version !== version ||
        typeof message.channel !== "string" ||
        message.channel.length > 128
      )
        return;
      if (message.type === "connect") {
        // One controller per embedded page; a new controller reloads the iframe.
        if (channel && channel !== message.channel) return;
        channel = message.channel;
        send({ type: "connected", state: player.state });
        return;
      }
      if (
        !channel ||
        message.channel !== channel ||
        message.type !== "command" ||
        !Number.isSafeInteger(message.id) ||
        !Array.isArray(message.args)
      )
        return;
      const { id, method, args } = message;
      try {
        switch (method) {
          case "load":
            await player.load(args[0], args[1]);
            break;
          case "play":
            player.play();
            break;
          case "pause":
            player.pause();
            break;
          case "seek":
            player.seek(args[0]);
            break;
          case "setPlaybackRate":
            player.setPlaybackRate(args[0]);
            break;
          case "setMuted":
            player.setMuted(args[0]);
            break;
          case "setVolume":
            player.setVolume(args[0]);
            break;
          case "setView":
            player.setView(args[0]);
            break;
          case "fitCamera":
            player.fitCamera();
            break;
          case "exitAR":
            await player.exitAR();
            break;
          case "getState":
            break;
          default:
            throw new Error(`Unknown player command: ${method}`);
        }
        send({ type: "reply", id, state: player.state });
      } catch (error) {
        send({
          type: "reply",
          id,
          error: { name: error.name, message: error.message },
          state: player.state,
        });
      }
    },
    { signal: events.signal },
  );
  for (const type of PLAYER_EVENTS) {
    player.addEventListener(
      type,
      ({ detail }) => {
        if (channel) send({ type: "event", event: type, state: detail });
        if (type === "destroy") events.abort();
      },
      { signal: events.signal },
    );
  }
  return () => events.abort();
}
