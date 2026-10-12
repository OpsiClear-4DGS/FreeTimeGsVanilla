// Continue on a new task without nested setTimeout's 4 ms clamp.
export function yieldToBrowser() {
  if (globalThis.scheduler?.yield) return globalThis.scheduler.yield();
  return new Promise((resolve) => {
    const { port1, port2 } = new MessageChannel();
    port1.onmessage = () => { port1.close(); port2.close(); resolve(); };
    port2.postMessage(null);
  });
}
