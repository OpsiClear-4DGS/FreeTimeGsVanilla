"""Optional end-to-end checks; requires playwright and Pillow, not app packages."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import shutil
import threading

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Existing Chromium executable")
    parser.add_argument("--model", type=Path, help="An exported .ftgs.ply to also test")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.model:
        args.model = args.model.resolve(strict=True)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/test-model.ftgs.ply" and args.model:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(args.model.stat().st_size))
                self.end_headers()
                with args.model.open("rb") as model:
                    shutil.copyfileobj(model, self.wfile)
                return
            super().do_GET()

    root = Path(__file__).resolve().parents[1]
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    def ready(page):
        page.wait_for_function(
            "document.querySelector('#viewer').dataset.state === 'ready' && "
            "document.querySelector('#viewer').dataset.loaded === 'true'"
        )

    def drop_demo(page):
        transfer = page.evaluate_handle("""async () => {
          const {demoFile} = await import('/demo.js');
          const transfer = new DataTransfer();
          transfer.items.add(new File([demoFile()], 'ribbon.ftgs.ply'));
          return transfer;
        }""")
        page.dispatch_event("#viewer", "dragover", {"dataTransfer": transfer})
        assert page.locator("#drop-hint").is_visible()
        page.dispatch_event("#viewer", "drop", {"dataTransfer": transfer})
        ready(page)
        assert not page.locator("#drop-hint").is_visible()
        transfer.dispose()

    def seek(page, t):
        page.locator("#timeline").evaluate(
            "(el, t) => { el.value = t; el.dispatchEvent(new Event('input')); }", t
        )
        page.wait_for_timeout(400)  # Allow a queued worker frame and the new frame to finish.

    def scene_image(page):
        box = page.locator("#canvas").bounding_box()
        assert box
        # Ignore all status/transport overlays so differences measure the actual render.
        clip = dict(x=box["x"] + box["width"] * 0.2,
                    y=box["y"] + box["height"] * 0.25,
                    width=box["width"] * 0.6, height=box["height"] * 0.45)
        return Image.open(BytesIO(page.screenshot(clip=clip))).convert("RGB")

    def difference(a, b):
        return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=args.browser,
                headless=True,
                args=["--no-sandbox", "--use-angle=swiftshader", "--enable-webgl",
                      "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
            )
            errors = []
            page = browser.new_page(viewport={"width": 1440, "height": 930})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto(url)
            assert page.locator("#empty").is_visible()
            assert not page.locator("#controls").is_visible()
            assert page.locator("header, aside, footer").count() == 0
            if args.screenshots:
                page.screenshot(path=args.screenshots / "empty.png")
            # Check initial-file failure leaves an accessible way to try again.
            with page.expect_file_chooser() as chooser:
                page.locator("#empty").click()
            chooser.value.set_files({"name": "invalid.ftgs.ply", "mimeType": "application/octet-stream", "buffer": b"invalid"})
            page.wait_for_function("document.querySelector('#viewer').dataset.state === 'error'")
            assert page.locator("#open-file").is_visible()
            drop_demo(page)
            assert not page.locator("#empty").is_visible()
            assert page.locator("#play-toggle").get_attribute("aria-label") == "Pause"
            page.mouse.move(100, 100)
            page.wait_for_function("getComputedStyle(document.querySelector('#controls')).opacity === '0'", timeout=6000)
            page.mouse.move(110, 110)
            page.wait_for_function("getComputedStyle(document.querySelector('#controls')).opacity === '1'")
            projection = page.evaluate("""async () => {
              const {SplatRenderer} = await import('/renderer.js');
              const canvas = document.createElement('canvas');
              canvas.style.cssText = 'position:fixed;left:-1000px;width:64px;height:64px';
              document.body.append(canvas);
              const renderer = new SplatRenderer(canvas), gl = renderer.gl;
              const camera = {view: new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]),
                eye: [0,0,0], near: 0.001, fov: Math.PI/4};
              const colors = [];
              for (const x of [0, 1000]) {
                renderer.setModel({
                  positionTime: new Float32Array([x,0,-0.02,0.5]),
                  velocityDuration: new Float32Array([0,0,0,1]),
                  covarianceA: new Float32Array([1,0,0,1]),
                  covarianceB: new Float32Array([0,1,1,0]),
                  sh: new Float32Array([1,1,1,0]), coefficients: 1,
                  useVelocity: false, opacityFloor: 0.0001,
                });
                renderer.draw(new Uint32Array([0]), camera, 0.5, 0, 1);
                const pixel = new Uint8Array(4);
                gl.readPixels(canvas.width/2,canvas.height/2,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
                colors.push([...pixel]);
              }
              renderer.destroy(); canvas.remove(); return colors;
            }""")
            assert min(projection[0][:3]) > 100, "Centered Gaussian did not render"
            assert max(projection[1][:3]) < 20, "Offscreen Gaussian stretched over the viewport"
            seek(page, 0.15)
            early = scene_image(page)
            assert sum(ImageStat.Stat(early).stddev) > 10, "Canvas is blank"
            seek(page, 0.7)
            late = scene_image(page)
            assert difference(early, late) > 2, "Seeking did not animate the scene"
            box = page.locator("#canvas").bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 120, y + 30, steps=8)
            page.mouse.up()
            page.wait_for_timeout(400)
            assert difference(late, scene_image(page)) > 2, "Orbit did not change the view"
            page.locator("#canvas").focus()
            before = float(page.locator("#timeline").input_value())
            page.keyboard.press("ArrowRight")
            assert float(page.locator("#timeline").input_value()) > before
            page.keyboard.press("r")
            seek(page, 0.99)
            page.locator("#play-toggle").click()
            page.wait_for_function("Number(document.querySelector('#timeline').value) < 0.5")
            assert page.locator("#play-toggle").get_attribute("aria-label") == "Pause"
            page.locator("#file").set_input_files(
                {"name": "invalid.ftgs.ply", "mimeType": "application/octet-stream", "buffer": b"invalid"}
            )
            page.wait_for_function("document.querySelector('#viewer').dataset.state === 'error'")
            assert "header" in page.locator("#message").inner_text()
            drop_demo(page)
            if args.model:
                with page.expect_file_chooser() as chooser:
                    page.locator("#open-file").click()
                chooser.value.set_files(args.model)
                page.wait_for_function("name => document.title.startsWith(name)", arg=args.model.name)
                ready(page)
                page.goto(f"{url}/?src=/test-model.ftgs.ply&points=all")
                ready(page)
                assert page.title().startswith("test-model.ftgs.ply")
                drop_demo(page)
            seek(page, 0.3)
            assert page.locator("#canvas").evaluate("c => c.getContext('webgl2').getError()") == 0
            if args.screenshots:
                page.screenshot(path=args.screenshots / "desktop.png")
            mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            mobile.on("pageerror", lambda error: errors.append(str(error)))
            mobile.goto(url)
            assert mobile.locator("#empty").is_visible()
            drop_demo(mobile)
            seek(mobile, 0.3)
            assert mobile.evaluate("document.documentElement.scrollWidth") == 390
            assert mobile.locator("#canvas").bounding_box()["height"] == 844
            if args.screenshots:
                mobile.screenshot(path=args.screenshots / "mobile.png")
            assert not errors, errors
            browser.close()
        print("PASS: rendered pixels, animation, orbit, controls, load recovery, mobile layout"
              + (", exported file and URL loading" if args.model else ""))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
