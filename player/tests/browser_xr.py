"""Native WebXR integration with a simulated device and real WebGL/worker rendering."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Existing Chromium executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/host.html":
                data = b'''<!doctype html><title>XR host</title><link rel="icon" href="data:,">
                  <canvas id="scene" style="width:640px;height:480px"></canvas>'''
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(data)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True, args=[
                "--no-sandbox", "--use-angle=swiftshader", "--enable-webgl",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            errors = []
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_init_script(path=root / "tests/xr-mock.js")
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url + "/host.html")
            page.evaluate('''async () => {
              const {FTGSPlayer} = await import('./player.js');
              const {demoFile} = await import('./demo.js');
              Object.assign(window, {FTGSPlayer, demo: demoFile(), xr: navigator.xr});
              window.check = (value, message) => { if (!value) throw new Error(message); };
              window.wait = () => new Promise(resolve => setTimeout(resolve, 100));
              window.holdSort = false; window.heldSort = [];
              const Base = Worker;
              window.Worker = class extends Base {
                set onmessage(callback) {
                  super.onmessage = event => {
                    if (holdSort) heldSort.push(() => callback(event));
                    else callback(event);
                  };
                }
              };
              window.player = new FTGSPlayer(document.querySelector('#scene'), {autoplay:false});
              await player.load(demo);
              await wait();
              const gl = document.querySelector('#scene').getContext('webgl2');
              window.desktopView = Array.from(gl.getUniform(gl.getParameter(gl.CURRENT_PROGRAM), gl.getUniformLocation(gl.getParameter(gl.CURRENT_PROGRAM),'view')));
              await player.enterAR();
              check(player.state.ar.status === 'presenting', 'AR did not start');
              check(xr.lastRequest.mode === 'immersive-ar', 'Wrong session mode');
              check(xr.lastRequest.options.requiredFeatures.includes('local'), 'No tracked reference space');
              check(xr.session.renderState.baseLayer.options.alpha, 'XR layer is opaque');
              xr.session.step(); await wait(); xr.session.step();
              check(xr.draws.length === 2, 'Did not draw both eye views');
              const [left,right] = xr.draws;
              check(left.viewport[0] === 0 && right.viewport[0] === 320, 'Stereo viewports are wrong');
              check(left.projection[8] > 0 && right.projection[8] < 0, 'Asymmetric eye projections were lost');
              check(left.view[12] !== right.view[12], 'Both eyes use the same camera');
              const pixels = xr.pixels();
              check(pixels.count.every(n => n > 100), 'One or both eyes are blank');
              check(pixels.corner.every(n => n === 0), 'AR background is not transparent black');
              check(pixels.error === 0, 'WebGL error in XR');
              check(!player.playing && player.time === 0, 'Entering AR started paused playback');

              xr.session.dispatchEvent(new Event('select'));
              check(player.state.ar.placed, 'Tap did not fix placement');
              holdSort = true;
              xr.session.step({x:0.2}); await wait();
              const before = xr.draws.at(-1).view[12];
              xr.session.step({x:0.4});
              check(Math.abs(xr.draws.at(-1).view[12] - before + 0.2) < 1e-5, 'Tracking waited for worker sorting');
              check(heldSort.length > 0, 'Slow-worker test did not hold a result');
              holdSort = false; heldSort.splice(0).forEach(release => release());

              player.play();
              xr.session.step({advance:100}); await wait(); xr.session.step({advance:100});
              check(player.time > 0, 'Animation did not advance in XR');
              player.pause();
              const time = player.time;
              xr.session.step({advance:100});
              check(player.time === time, 'Pause did not work in XR');
              player.seek(.8);
              xr.session.step(); await wait(); xr.session.step(); await wait(); xr.session.step();
              check(Math.abs(xr.draws.at(-1).time - .8) < 1e-6, 'Seek did not reach XR rendering');
              const draws = xr.draws.length;
              xr.session.step({tracked:false});
              check(xr.draws.length === draws, 'Drew a stale pose when tracking was lost');
              check(xr.pixels().count.every(n => n === 0), 'Tracking loss left a stale image');
              player.fitCamera();
              check(!player.state.ar.placed, 'Reposition did not reset placement');
              xr.session.step({surface:[0,-.3,-1.5]});
              check(player.state.ar.surface, 'Surface detection was not used');
              xr.session.dispatchEvent(new Event('select'));
              check(player.state.ar.placed, 'Surface placement did not lock');
              const session = xr.session;
              await player.exitAR(); await wait();
              check(session.ended && session.endCount === 1 && session.cancelledSources === 1, 'Session resources leaked');
              check(session.callbacks.size === 0 && player.state.ar.status === 'inactive', 'XR animation leaked');
              check(gl.getParameter(gl.FRAMEBUFFER_BINDING) === null, 'Desktop framebuffer was not restored');
              const view = gl.getUniform(gl.getParameter(gl.CURRENT_PROGRAM), gl.getUniformLocation(gl.getParameter(gl.CURRENT_PROGRAM),'view'));
              check(view.every((v,i) => Math.abs(v-desktopView[i]) < 1e-5), 'Desktop camera was changed by AR');
              check(xr.violations.length === 0, xr.violations.join(', '));
            }''')
            print("PASS: stereo pixels, transparent background, asymmetric projections, live tracking with slow sorting, animation/seek, surface placement and desktop restoration", flush=True)
            page.evaluate('''async () => {
              xr.reject = true;
              try { await player.enterAR(); throw new Error('Permission denial was ignored'); }
              catch (error) { check(error.name === 'NotAllowedError', error.message); }
              check(player.state.status === 'ready' && player.state.ar.status === 'inactive', 'Denial broke desktop playback');
              xr.reject = false; xr.failSpace = true;
              try { await player.enterAR(); throw new Error('Reference-space failure was ignored'); }
              catch (error) { check(error.message.includes('Reference space'), error.message); }
              check(xr.session.ended && player.state.ar.status === 'inactive', 'Partial startup leaked a session');
              xr.failSpace = false; xr.hitTest = false;
              await player.enterAR();
              check(!player.state.ar.hitTest && !player.state.ar.error, 'Optional feature fallback failed');
              xr.session.step({stereo:false}); await wait(); xr.session.step({stereo:false});
              check(xr.draws.at(-1).viewport[2] === 640, 'Monoscopic AR viewport is wrong');
              const session = xr.session;
              await player.load(demo);
              check(session.ended && player.state.ar.status === 'inactive', 'Replacing a model did not exit AR');
              await player.enterAR();
              const external = xr.session;
              await external.end();
              check(player.state.ar.status === 'inactive', 'Device exit did not clean up');
              xr.defer = true;
              const pending = player.enterAR().catch(error => error.name);
              player.destroy();
              xr.resolveRequest();
              check(await pending === 'AbortError', 'Destroyed startup did not reject');
              check(xr.session.ended && xr.session.callbacks.size === 0, 'Late permission grant leaked AR');
              check(player.state.status === 'destroyed', 'Late startup revived destroyed player');
            }''')
            print("PASS: permission denial, setup failure, optional hit-test fallback, mono views, reload/device exit, destruction during permission request", flush=True)

            # Real user gesture, minimal responsive UI, and overlay hit suppression.
            page.goto(url + "/?demo=1&autoplay=0")
            page.wait_for_selector('#viewer[data-loaded="true"]')
            page.wait_for_function("!document.querySelector('#ar').hidden && !document.querySelector('#ar').disabled")
            assert page.evaluate("document.documentElement.scrollWidth") == 390
            page.set_viewport_size({"width": 320, "height": 640})
            assert page.locator("#ar").bounding_box()["x"] > 0
            assert page.locator("#fullscreen").bounding_box()["x"] + 42 <= 320
            page.locator("#ar").click()
            page.wait_for_selector('#viewer[data-ar="presenting"]')
            assert page.evaluate("navigator.xr.lastRequest.userGesture"), "Session request lost user activation"
            assert page.locator("#ar").get_attribute("aria-label") == "Exit AR"
            assert not page.locator("#canvas").is_visible(), "Canvas would cover camera in DOM overlay"
            assert page.locator("#ar-hint").is_visible()
            assert page.evaluate('''() => {
              const event = new Event('beforexrselect', {bubbles:true,cancelable:true});
              document.querySelector('#play-toggle').dispatchEvent(event);
              return event.defaultPrevented;
            }'''), "Overlay buttons also trigger scene placement"
            page.evaluate("navigator.xr.session.step({surface:[0,-.3,-1.5]})")
            assert "surface" in page.locator("#ar-hint").inner_text()
            page.evaluate("navigator.xr.session.dispatchEvent(new Event('select'))")
            assert "Placed" in page.locator("#ar-hint").inner_text()
            page.locator("#reset-view").click()
            assert "Placed" not in page.locator("#ar-hint").inner_text()
            page.locator("#ar").click()
            page.wait_for_selector('#viewer[data-ar="inactive"]')
            assert page.locator("#canvas").is_visible()
            assert not page.locator("#ar-hint").is_visible()
            assert page.locator("#fullscreen").is_visible()
            page.evaluate("navigator.xr.supported = false; navigator.xr.dispatchEvent(new Event('devicechange'))")
            page.wait_for_function("document.querySelector('#ar').hidden")
            print("PASS: AR button gesture, mobile layout, overlay controls, reposition/exit and unsupported-device fallback", flush=True)

            # The iframe demo explicitly delegates XR and enters in the child.
            page.goto(url + "/examples/embedding.html?mode=iframe")
            frame = page.frame_locator("iframe")
            frame.locator('#viewer[data-loaded="true"]').wait_for()
            assert "xr-spatial-tracking" in page.locator("iframe").get_attribute("allow")
            frame.locator("#ar").click()
            frame.locator('#viewer[data-ar="presenting"]').wait_for()
            frame.locator("#ar").click()
            frame.locator('#viewer[data-ar="inactive"]').wait_for()
            page.goto(url + "/host.html")
            page.evaluate('''async childURL => {
              const {FTGSEmbed} = await import('./embed.js');
              const {demoFile} = await import('./demo.js');
              const frame = document.createElement('iframe');
              frame.allow = 'xr-spatial-tracking';
              frame.style.cssText = 'width:320px;height:480px';
              document.body.replaceChildren(frame);
              window.embed = new FTGSEmbed(frame, {src:childURL});
              window.arEvents = [];
              embed.addEventListener('arstatechange', ({detail}) => arEvents.push(detail.ar.status));
              await embed.ready;
              await embed.load(demoFile());
            }''', url.replace("127.0.0.1", "localhost") + "/?autoplay=0")
            frame = page.frame_locator("iframe")
            frame.locator("#ar").click()
            page.wait_for_function("embed.state.ar.status === 'presenting'")
            page.evaluate("async () => { await embed.fitCamera(); await embed.exitAR(); }")
            frame.locator('#viewer[data-ar="inactive"]').wait_for()
            assert page.evaluate("arEvents.includes('starting') && arEvents.includes('presenting') && arEvents.includes('inactive')")
            assert page.evaluate("embed.state.loaded && !embed.state.playing")
            page.evaluate("embed.destroy()")
            assert not errors, errors
            browser.close()
            print("PASS: iframe AR entry, cross-origin state events and host exit with delegated tracking; no browser exceptions", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
