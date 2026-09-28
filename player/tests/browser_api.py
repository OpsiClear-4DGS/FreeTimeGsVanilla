"""Browser integration checks for canvas and cross-origin iframe APIs (Playwright)."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
import threading
import time

from playwright.sync_api import sync_playwright


def model_file():
    fields = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
              "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
              "time", "log_duration", "velocity_0", "velocity_1", "velocity_2"]
    header = "\n".join([
        "ply", "format binary_little_endian 1.0", "comment ftgs_version 1",
        "comment time_units normalized", "comment sh_degree 0", "comment use_velocity 1",
        "comment min_duration 0.02", "comment opacity_floor 0.0001", "comment n_frames 61",
        "element vertex 1", *(f"property float {field}" for field in fields), "end_header", "",
    ])
    values = {"opacity": 2, "rot_0": 1, "time": 0.5, "velocity_0": 1}
    return header.encode() + struct.pack("<" + "f" * len(fields), *(values.get(field, 0) for field in fields))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Existing Chromium executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    requests = []
    prefix = "/ftgs-player/"
    host_html = b'''<!doctype html><html><head><title>Host project</title>
      <link rel="icon" href="data:,"></head><body style="margin:20px;background:white;color:black">
      <h1>Host project</h1><input id="host-input" aria-label="Host input">
      <canvas id="a" tabindex="0" style="width:320px;height:240px;touch-action:none"></canvas>
      <canvas id="b" tabindex="0" style="width:320px;height:240px;touch-action:none"></canvas>
      <iframe id="embedded" title="Embedded player" style="width:640px;height:360px;border:0"></iframe>
      <footer>Host footer</footer></body></html>'''

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/host.html":
                data, content_type = host_html, "text/html"
            elif path in ["/scene.ftgs.ply", "/slow.ftgs.ply"]:
                requests.append((self.server.server_port, path))
                if path.startswith("/slow"):
                    time.sleep(0.6)
                data, content_type = model_file(), "application/octet-stream"
            elif path.startswith(prefix):
                self.path = "/" + self.path[len(prefix):]
                try:
                    return super().do_GET()
                except (BrokenPipeError, ConnectionResetError):
                    return  # Expected when destroying an iframe during navigation.
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root))) for _ in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    host, child = [f"http://127.0.0.1:{s.server_port}" for s in servers]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True, args=[
                "--no-sandbox", "--use-angle=swiftshader", "--enable-webgl",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            page = browser.new_page(viewport={"width": 1200, "height": 960})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto(host + "/host.html")
            page.evaluate('''async () => {
              window.check = (condition, message) => { if (!condition) throw new Error(message); };
              const {FTGSPlayer} = await import('/ftgs-player/player.js');
              const {FTGSEmbed} = await import('/ftgs-player/embed.js');
              const {demoFile} = await import('/ftgs-player/demo.js');
              Object.assign(window, {FTGSPlayer, FTGSEmbed, demo: demoFile()});
              window.live = {workers: new Set(), frames: new Set(), observers: new Set()};
              const WorkerBase = Worker, ObserverBase = ResizeObserver;
              window.Worker = class extends WorkerBase {
                constructor(...args) { super(...args); live.workers.add(this); }
                terminate() { live.workers.delete(this); super.terminate(); }
              };
              window.ResizeObserver = class extends ObserverBase {
                constructor(...args) { super(...args); live.observers.add(this); }
                disconnect() { live.observers.delete(this); super.disconnect(); }
              };
              const raf = requestAnimationFrame, cancel = cancelAnimationFrame;
              window.requestAnimationFrame = callback => {
                const id = raf(now => { live.frames.delete(id); callback(now); });
                live.frames.add(id); return id;
              };
              window.cancelAnimationFrame = id => { live.frames.delete(id); cancel(id); };
              window.a = new FTGSPlayer(document.querySelector('#a'), {autoplay:false, maxPoints:12000});
              window.b = new FTGSPlayer(document.querySelector('#b'), {autoplay:false, loop:false, frames:4, fps:60, maxPoints:8000});
              window.events = [];
              for (const type of ['loaded','play','pause','timeupdate','error','abort','destroy'])
                a.addEventListener(type, e => events.push([type, e.detail]));
              await Promise.all([a.load(new File([demo], 'first.ftgs.ply')), b.load(demo)]);
              check(a.state.name === 'first.ftgs.ply', 'File name lost');
              check(a.state.pointCount === 12000 && b.state.pointCount === 8000, 'Instance options leaked');
              check(a.state.duration === 6 && !a.playing, 'Autoplay or duration incorrect');
              const snapshot = a.state; snapshot.time = 99;
              check(a.time === 0, 'Mutable state escaped');
              try { new FTGSPlayer(document.querySelector('#a')); throw new Error('Duplicate owner accepted'); }
              catch(e) { check(e.message.includes('already'), e.message); }
              a.seek(.25); b.seek(.75);
              check(a.time === .25 && b.time === .75, 'Independent seeking failed');
              a.seek(10); check(a.time === 1, 'Seek upper clamp failed');
              a.seek(-1); check(a.time === 0, 'Seek lower clamp failed');
              try { a.seek(NaN); throw new Error('Invalid seek accepted'); }
              catch(e) { check(e instanceof TypeError, 'Invalid seek did not throw TypeError'); }
              a.play();
            }''')
            page.wait_for_function("a.time > .03")
            page.evaluate("check(b.time === .75 && !b.playing, 'Playback affected another instance'); a.pause(); a.seek(.4)")
            page.locator("#host-input").fill("Host text")
            page.keyboard.press("Space")
            assert page.locator("#host-input").input_value() == "Host text "
            page.evaluate("check(a.time === .4 && !a.playing, 'Host keyboard affected player')")
            assert page.title() == "Host project"
            assert page.locator("footer").is_visible()
            page.evaluate('''async () => {
              window.ended = new Promise(resolve => b.addEventListener('ended', resolve, {once:true}));
              b.play(); await ended;
              check(b.time === 1 && !b.playing, 'Non-looping playback did not stop');
              b.play(); check(b.time === 0 && b.playing, 'Replay did not restart'); b.pause();
              try { await a.load(new Blob(['invalid'])); throw new Error('Invalid PLY accepted'); }
              catch(e) { check(e.message.includes('header'), e.message); }
              check(a.state.loaded && a.state.status === 'error' && !a.playing, 'Load failure lost working model');
              a.play(); check(a.playing, 'Could not resume previous model'); a.pause();
              const controller = new AbortController();
              a.addEventListener('progress', () => controller.abort(), {once:true});
              try { await a.load(demo, {signal:controller.signal}); throw new Error('Abort ignored'); }
              catch(e) { check(e.name === 'AbortError', e.message); }
              check(a.state.status === 'ready', 'Abort did not recover previous model');
              const first = a.load('/slow.ftgs.ply').catch(e => e.name);
              await a.load('/scene.ftgs.ply');
              check(await first === 'AbortError', 'Superseded load did not reject');
              check(a.state.name === 'scene.ftgs.ply' && a.state.pointCount === 1, 'URL load failed');
              const view = {eye:[0,-3,1], target:[0,0,0], up:'z', fov:45};
              a.setView(view); a.fitCamera(); a.setUpAxis('y');
              const before = a.state.name;
              try { await a.load(demo, {view:{eye:[0,0,0],target:[0,0,0]}}); }
              catch(e) { check(e.message.includes('distinct'), e.message); }
              check(a.state.name === before, 'Invalid camera replaced working model');
              await a.load(demo, {view}); a.pause();
            }''')
            page.wait_for_timeout(400)
            page.evaluate('''() => {
              const gl = document.querySelector('#a').getContext('webgl2');
              check(gl.getError() === gl.NO_ERROR, 'GL error after public API calls');
              window.program = gl.getParameter(gl.CURRENT_PROGRAM);
              check(program && gl.isProgram(program), 'No model was drawn');
              window.stopped = a.load('/slow.ftgs.ply').catch(e => e.name);
            }''')
            page.wait_for_timeout(100)
            page.evaluate('''async () => {
              a.destroy(); a.destroy(); b.destroy();
              check(await stopped === 'AbortError', 'Destroy did not cancel loading');
              check(a.state.status === 'destroyed' && !a.state.loaded, 'Destroyed state incorrect');
              check(!document.querySelector('#a').getContext('webgl2').isProgram(program), 'GPU program leaked');
              check(!live.workers.size && !live.frames.size && !live.observers.size, 'Resources leaked on destroy');
              try { a.play(); throw new Error('Destroyed player played'); }
              catch(e) { check(e.message.includes('destroyed'), e.message); }
              window.c = new FTGSPlayer(document.querySelector('#a'), {frames:1});
              await c.load(demo); c.play(); check(!c.playing, 'Single-frame model played'); c.destroy();
              check(!live.workers.size && !live.frames.size && !live.observers.size, 'Remount leaked resources');
              check(events.some(e => e[0] === 'loaded') && events.some(e => e[0] === 'error') && events.some(e => e[0] === 'abort'), 'Missing lifecycle events');
            }''')
            print("PASS: canvas API, independent instances, URL/File loading, events, seek, end/replay, cancellation, teardown/remount", flush=True)

            page.evaluate('''async child => {
              window.messages = [];
              addEventListener('message', e => { if(e.data?.protocol === 'ftgs-player') messages.push(e.data); });
              window.embed = new FTGSEmbed(document.querySelector('#embedded'), {
                src: child + '/ftgs-player/?autoplay=0&controls=0&points=10000',
              });
              window.embedEvents = [];
              for (const type of ['loaded','play','pause','timeupdate','error'])
                embed.addEventListener(type, e => embedEvents.push([type, e.detail]));
              await embed.ready;
              check(embed.state.status === 'empty', 'Iframe readiness state incorrect');
              await embed.load(new File([demo], 'embedded.ftgs.ply'));
              check(embed.state.name === 'embedded.ftgs.ply' && !embed.state.playing, 'Iframe File load failed');
              await embed.seek(.3); check(embed.state.time === .3, 'Iframe seek failed');
              await embed.play();
            }''', child)
            page.wait_for_function("embed.state.time > .33")
            page.evaluate("embed.pause()")
            frame = next(f for f in page.frames if f.url.startswith(child + prefix))
            assert frame.locator("#controls").is_hidden()
            assert frame.locator("#canvas").bounding_box()["width"] == 640
            assert page.title() == "Host project"
            page.locator("#embedded").evaluate("el => { el.style.width='320px'; el.style.height='220px'; }")
            frame.wait_for_function("document.querySelector('#canvas').clientWidth === 320")
            page.evaluate('''async () => {
              await embed.load('scene.ftgs.ply');
              check(embed.state.name === 'scene.ftgs.ply' && embed.state.pointCount === 1, 'Host-relative URL load failed');
              try { await embed.load(new Blob(['invalid'])); throw new Error('Invalid iframe load accepted'); }
              catch(e) { check(e.message.includes('header'), e.message); }
              const first = embed.load('/slow.ftgs.ply').catch(e => e.name);
              await embed.load(demo); check(await first === 'AbortError', 'Iframe superseded load did not reject');
              await embed.seek(.6);
              check((await embed.getState()).time === .6, 'Iframe state request failed');
              try { await embed.seek(Infinity); throw new Error('Invalid iframe seek accepted'); }
              catch(e) { check(e.name === 'TypeError', e.message); }
              await embed.setView({eye:[0,-4,1],target:[0,0,0],up:'z'});
              await embed.fitCamera();
              check(embedEvents.some(e => e[0] === 'loaded') && embedEvents.some(e => e[0] === 'timeupdate') && embedEvents.some(e => e[0] === 'error'), 'Iframe events missing');
              window.channel = messages.find(m => m.type === 'connected').channel;
              window.fake = {protocol:'ftgs-player', version:1, channel, type:'command', id:999, method:'seek', args:[.9]};
              const sibling = document.createElement('iframe'); sibling.id='sibling'; document.body.append(sibling);
            }''')
            sibling = page.frames[-1]
            sibling.evaluate("message => parent.document.querySelector('#embedded').contentWindow.postMessage(message, '*')", page.evaluate("fake"))
            frame.evaluate('''message => {
              window.dispatchEvent(new MessageEvent('message', {source:parent, origin:'https://invalid.example', data:message}));
            }''', page.evaluate("fake"))
            page.evaluate('''child => {
              document.querySelector('#embedded').contentWindow.postMessage({...fake, channel:'unrelated'}, child);
              window.postMessage({protocol:'ftgs-player', version:1, channel, type:'event', event:'timeupdate', state:{time:999}}, location.origin);
            }''', child)
            page.wait_for_timeout(200)
            page.evaluate("async () => check((await embed.getState()).time === .6 && embed.state.time !== 999, 'Untrusted message accepted')")
            assert (servers[0].server_port, "/scene.ftgs.ply") in requests
            assert (servers[1].server_port, "/scene.ftgs.ply") not in requests
            page.evaluate('''async child => {
              const pending = embed.load('/slow.ftgs.ply').catch(e => e.name);
              embed.destroy(); embed.destroy();
              check(['AbortError','Error'].includes(await pending), 'Destroy did not reject pending request');
              check(document.querySelector('#embedded').getAttribute('src') === 'about:blank', 'Destroy did not unload iframe');
              const unmounted = new FTGSEmbed(document.querySelector('#embedded'), {src:child + '/ftgs-player/'});
              unmounted.destroy();
              try { await unmounted.ready; throw new Error('Early unmount resolved ready'); }
              catch(e) { check(e.name === 'AbortError', e.message); }
              window.embed = new FTGSEmbed(document.querySelector('#embedded'), {src:child + '/ftgs-player/'});
              await embed.ready; await embed.load(demo, {autoplay:false}); embed.destroy();
              const timeout = new FTGSEmbed(document.querySelector('#embedded'), {src:child + '/host.html', timeout:500});
              try { await timeout.ready; throw new Error('Missing bridge connected'); }
              catch(e) { check(e.message.includes('timed out'), e.message); }
              timeout.destroy();
            }''', child)
            assert not errors, errors
            print("PASS: cross-origin iframe API, host-relative URLs with CORS, events, origin/source checks, resize, failure/reconnect/cleanup", flush=True)
            for mode in ["canvas", "iframe"]:
                page.goto(host + prefix + "examples/embedding.html?mode=" + mode)
                page.wait_for_function("!document.querySelector('#play').disabled")
                page.locator("#play").click()
                page.wait_for_function("Number(document.querySelector('#timeline').value) > .03")
                page.locator("#play").click()
                page.locator("#timeline").evaluate("el => { el.value = .5; el.dispatchEvent(new Event('input')); }")
                page.wait_for_function("Number(document.querySelector('#timeline').value) === .5")
                assert page.title() == "Embedding an FTGS player"
                assert page.locator("#message").inner_text() == ""
            assert not errors, errors
            print("PASS: runnable canvas and iframe documentation examples", flush=True)
            browser.close()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


if __name__ == "__main__":
    main()
