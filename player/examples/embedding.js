// Both integration paths use the same player presentation.
if (new URLSearchParams(location.search).get("mode") === "iframe") {
  const { FTGSEmbed } = await import("../embed.js?v=4");
  const { demoFile } = await import("../demo.js");
  const frame = document.createElement("iframe");
  frame.className = "embedded-player";
  frame.title = "FTGS player";
  frame.allowFullscreen = true;
  frame.allow = "xr-spatial-tracking";
  document.querySelector("#viewer").replaceWith(frame);
  const player = new FTGSEmbed(frame, {
    src: new URL("../?autoplay=0", import.meta.url).href,
  });
  try {
    await player.ready;
    await player.load(demoFile(), { name: "Generated demo" });
  } catch (error) {
    if (error.name !== "AbortError") {
      const message = document.createElement("p");
      message.className = "message";
      message.setAttribute("role", "status");
      message.textContent = error.message;
      document.body.append(message);
    }
  }
  addEventListener("pagehide", (event) => {
    if (!event.persisted) player.destroy();
  });
} else {
  // app.js binds this shell's controls to an independent FTGSPlayer canvas.
  await import("../app.js?v=4");
}
