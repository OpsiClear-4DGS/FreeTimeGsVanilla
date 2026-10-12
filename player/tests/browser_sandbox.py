"""Exercise built-in controls in cross-origin and opaque sandbox iframes.

This tests browser compatibility, not X's acceptance of a live Player Card.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Existing Chromium executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = subprocess.check_output(["node", "--input-type=module", "-e", """
      import { readFile } from 'node:fs/promises';
      import { packageTSOG } from './src/tsog-package.js';
      import { toneFile } from './tests/audio-fixture.mjs';
      const blob = await packageTSOG(new Blob([await readFile('tests/fixtures/tsog/continuous16.tsog')]), {
        audio: toneFile(), playback: {duration:4, fps:24, loop:true}, volume:1,
      });
      process.stdout.write(new Uint8Array(await blob.arrayBuffer()));
    """], cwd=root)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/host.html":
                flags = parse_qs(urlsplit(self.path).query).get("sandbox", [""])[0]
                sandbox = f' sandbox="{flags}"' if flags else ""
                target = f'http://127.0.0.1:{self.server.server_port}/?example=jensen-2m&autoplay=0'
                data = f'''<!doctype html><link rel="icon" href="data:,">
                  <style>body{{margin:0}}iframe{{display:block;width:100vw;height:100dvh;border:0}}</style>
                  <iframe{sandbox} src="{target}" allowfullscreen></iframe>'''.encode()
                mime = "text/html"
            elif path == "/examples/assets/jensen-2m.json":
                data = json.dumps({"file": "../../sandbox.tsog", "bytes": len(bundle)}).encode()
                mime = "application/json"
            elif path == "/sandbox.tsog":
                data, mime = bundle, "application/zip"
            else:
                return super().do_GET()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True, args=[
                "--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                "--enable-webgl", "--ignore-gpu-blocklist", "--disable-dev-shm-usage",
                "--autoplay-policy=document-user-activation-required",
            ])
            for flags in ["", "allow-scripts allow-same-origin", "allow-scripts"]:
                print(f"Checking iframe: {flags or 'no sandbox'}", flush=True)
                context = browser.new_context(viewport={"width": 480, "height": 300})
                context.add_init_script("""(() => {
                  const dispatch = EventTarget.prototype.dispatchEvent;
                  EventTarget.prototype.dispatchEvent = function(event) {
                    if (event.detail && typeof event.detail.pointCount === 'number') {
                      window.sceneState = event.detail;
                      window.scenePlayer = this;
                    }
                    return dispatch.call(this, event);
                  };
                  window.workers = new Set(); window.blobURLs = new Set();
                  const NativeWorker = Worker;
                  window.Worker = class extends NativeWorker {
                    constructor(...args) { super(...args); workers.add(this); }
                    terminate() { workers.delete(this); super.terminate(); }
                  };
                  const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
                  URL.createObjectURL = blob => { const url = create(blob); blobURLs.add(url); return url; };
                  URL.revokeObjectURL = url => { blobURLs.delete(url); revoke(url); };
                })();""")
                errors = []
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                page.goto(f"http://localhost:{server.server_port}/host.html?sandbox={flags.replace(' ', '+')}")
                page.frame_locator("iframe").locator('#viewer[data-loaded="true"]').wait_for()
                frame = page.frames[1]
                assert frame.evaluate("origin === 'null'") == (flags == "allow-scripts")
                assert frame.evaluate("!sceneState.playing && !sceneState.audio.muted && sceneState.audio.available")
                frame.wait_for_function("sceneState.status === 'error' || document.querySelector('#canvas').getContext('webgl2').getParameter(0x8B8D) !== null")
                assert frame.evaluate("sceneState.status !== 'error'"), (frame.locator("#message").inner_text(), errors)

                def image():
                    return Image.open(BytesIO(frame.locator("#canvas").screenshot())).convert("RGB")

                early = image()
                assert sum(ImageStat.Stat(early).stddev) > 1, "Sandbox scene is blank"
                frame.locator("#play-toggle").click()
                frame.wait_for_function("sceneState.playing && sceneState.time > .1 && sceneState.audio.status === 'playing'")
                frame.locator("#play-toggle").click()
                frame.locator("#timeline").evaluate("el => {el.value=.7;el.dispatchEvent(new Event('input'))}")
                frame.wait_for_timeout(250)
                assert sum(ImageStat.Stat(ImageChops.difference(early, image())).mean) > .1, "Playback did not change the render"
                frame.locator("#playback-rate").select_option("2")
                assert frame.evaluate("sceneState.playbackRate") == 2
                frame.locator("#fullscreen").click()
                page.wait_for_function("document.fullscreenElement !== null")
                page.evaluate("document.exitFullscreen()")
                page.set_viewport_size({"width": 320, "height": 568})
                frame.wait_for_timeout(100)
                assert frame.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Sandbox UI overflows"
                frame.evaluate("scenePlayer.destroy()")
                assert frame.evaluate("workers.size === 0 && blobURLs.size === 0"), "Sandbox resources leaked"
                assert not errors, errors
                context.close()
            browser.close()
        print("PASS: cross-origin and opaque iframe rendering, paused start, audio after Play, seek, speed, fullscreen, mobile layout and resource cleanup")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
