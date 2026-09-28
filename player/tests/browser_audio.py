"""Synthetic packaged audio, playback defaults, UI and embedding integration."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Existing Chromium executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # All media is generated in memory. No recorded scene or audio is used.
    bundle = subprocess.check_output(["node", "--input-type=module", "-e", """
      import { readFile } from 'node:fs/promises';
      import { packageTSOG } from './tsog-package.js';
      import { toneFile } from './tests/audio-fixture.mjs';
      const blob = await packageTSOG(new Blob([await readFile('tests/fixtures/tsog/continuous16.tsog')]), {
        audio: toneFile(), playback: {duration:4, fps:24, rate:1.25, loop:false}, volume:0.4,
      });
      process.stdout.write(new Uint8Array(await blob.arrayBuffer()));
    """], cwd=root)
    prefix = "/ftgs-player/"

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if not self.path.startswith(prefix):
                return self.send_error(404)
            path = self.path[len(prefix):].split("?", 1)[0]
            if path == "audio-demo.tsog":
                data, mime = bundle, "application/zip"
            elif path == "host.html":
                data = b'''<!doctype html><title>Audio test</title><link rel="icon" href="data:,">
                  <canvas id="scene" style="width:640px;height:360px"></canvas>
                  <button id="start">Play</button>'''
                mime = "text/html"
            else:
                self.path = "/" + self.path[len(prefix):]
                return super().do_GET()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}{prefix}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True, args=[
                "--no-sandbox", "--use-angle=swiftshader", "--enable-webgl",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                "--autoplay-policy=document-user-activation-required",
            ])
            context = browser.new_context(viewport={"width": 960, "height": 640})
            context.add_init_script("""(() => {
              window.audioElements = []; window.audioURLs = new Set();
              const Native = Audio;
              window.Audio = function(...args) {
                const audio = new Native(...args); audioElements.push(audio); return audio;
              };
              window.Audio.prototype = Native.prototype;
              const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
              URL.createObjectURL = blob => {
                const url = create(blob); if (blob.type.startsWith('audio/')) audioURLs.add(url); return url;
              };
              URL.revokeObjectURL = url => { audioURLs.delete(url); revoke(url); };
            })();""")
            errors = []
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "?src=audio-demo.tsog")
            # Evaluating JS through the automation protocol can grant transient
            # activation. Let the actual page attempt autoplay before inspecting it.
            page.wait_for_timeout(600)
            page.wait_for_function("document.querySelector('#mute-toggle').getAttribute('aria-label') === 'Enable audio'")
            assert page.locator("#playback-rate").input_value() == "1.25"
            assert page.evaluate("audioElements[0].paused"), "Autoplay denial should be visible, not ignored"
            page.locator("#canvas").focus()
            page.keyboard.press("m")
            page.wait_for_function("!audioElements[0].paused && audioElements[0].currentTime > 0.05")
            page.locator("#playback-rate").select_option("2")
            assert page.evaluate("audioElements[0].playbackRate") == 2
            page.locator("#canvas").focus()
            page.keyboard.press("Space")
            paused = page.evaluate("audioElements[0].currentTime")
            page.wait_for_timeout(120)
            assert abs(page.evaluate("audioElements[0].currentTime") - paused) < 0.01
            page.locator("#timeline").evaluate("el => { el.value=0.5; el.dispatchEvent(new Event('input')); }")
            assert abs(page.evaluate("audioElements[0].currentTime") - 2) < 0.01
            page.locator("#canvas").focus()
            page.keyboard.press("m")
            assert page.evaluate("audioElements[0].muted")
            page.set_viewport_size({"width": 320, "height": 600})
            page.evaluate("document.querySelector('#ar').hidden = false")
            boxes = page.locator(".transport > :not([hidden])").evaluate_all("""els => els
              .filter(e => getComputedStyle(e).display !== 'none')
              .map(e => ({id:e.id, left:e.getBoundingClientRect().left, right:e.getBoundingClientRect().right}))""")
            assert all(b["left"] >= 0 and b["right"] <= 320 for b in boxes), boxes
            assert all(a["right"] <= b["left"] + 1 for a, b in zip(boxes, boxes[1:])), boxes

            # Canvas API: real media clock, rate, seek, final stop, replay, URL cleanup.
            page.goto(url + "host.html")
            page.evaluate("""async () => {
              const {FTGSPlayer} = await import('./player.js?v=6');
              window.player = new FTGSPlayer(document.querySelector('#scene'), {autoplay:false});
              window.bundle = await (await fetch('./audio-demo.tsog')).blob();
              document.querySelector('#start').onclick = () => player.play();
              await player.load(bundle);
            }""")
            page.wait_for_function("audioElements[0].readyState >= 2")
            state = page.evaluate("player.state")
            assert (state["duration"], state["fps"], state["nFrames"], state["playbackRate"], state["loop"]) == (4, 24, 97, 1.25, False)
            assert state["audio"]["volume"] == 0.4
            page.locator("#start").click()
            page.wait_for_function("player.state.audio.status === 'playing' && player.time > 0.05")
            state = page.evaluate("player.state")
            assert abs(state["currentTime"] - state["audio"]["currentTime"]) < 0.2, state
            page.evaluate("player.setPlaybackRate(0.5); player.setVolume(0.2); player.setMuted(true); player.seek(0.5)")
            assert page.evaluate("audioElements[0].playbackRate === 0.5 && audioElements[0].volume === 0.2 && audioElements[0].muted")
            page.evaluate("player.pause(); player.seek(0.99); player.play()")
            page.wait_for_function("!player.playing && player.time === 1 && audioElements[0].paused")
            page.locator("#start").click()
            page.wait_for_function("player.playing && player.time > 0 && player.time < 0.1 && !audioElements[0].paused")

            # A failed load preserves the old paused track. Successful replacement releases it.
            page.evaluate("""async () => {
              try { await player.load(new Blob(['not a model'])); throw new Error('Accepted bad file'); }
              catch (error) { if (error.message === 'Accepted bad file') throw error; }
              if (audioURLs.size !== 1 || !audioElements[0].paused) throw new Error('Failed-load audio cleanup');
              await player.load('./tests/fixtures/tsog/static.tsog');
              if (audioURLs.size || player.state.audio.available) throw new Error('Track not released');
              const {packageTSOG} = await import('./tsog-package.js');
              await player.load(await packageTSOG(bundle, {playback:{loop:true}}));
              player.setPlaybackRate(1); player.seek(0.99); player.play();
            }""")
            page.wait_for_function("player.playing && player.time < 0.1 && audioElements.at(-1).currentTime < 0.4 && !audioElements.at(-1).paused")

            # An audio track shorter than the scene ends once and restarts on the scene's loop.
            page.evaluate("""async () => {
              const {packageTSOG} = await import('./tsog-package.js');
              const {toneFile} = await import('./tests/audio-fixture.mjs');
              await player.load(await packageTSOG(bundle, {audio:toneFile(0.2), playback:{duration:0.8, loop:true}}));
              player.play();
            }""")
            page.wait_for_function("player.playing && player.time > 0.3 && audioElements.at(-1).paused")
            page.wait_for_function("player.playing && player.time < 0.2 && !audioElements.at(-1).paused")

            # Static scenes can carry a timed soundtrack; unknown codecs fail only the audio.
            page.evaluate("""async () => {
              const {packageTSOG} = await import('./tsog-package.js');
              const {toneFile} = await import('./tests/audio-fixture.mjs');
              const still = await (await fetch('./tests/fixtures/tsog/static.tsog')).blob();
              await player.load(await packageTSOG(still, {audio:toneFile(), playback:{duration:4}}));
              if (!player.state.playable || player.state.nFrames !== 1) throw new Error('Static soundtrack timing');
              await player.load(await packageTSOG(bundle, {audio:new File(['bad audio'], 'track.wav')}));
              player.play();
            }""")
            page.wait_for_function("player.state.audio.status === 'error'")
            assert page.evaluate("player.playing && player.state.status === 'ready'")
            page.evaluate("player.destroy()")
            assert page.evaluate("audioURLs.size === 0 && audioElements.every(a => a.paused && !a.getAttribute('src'))")

            # Explicit query settings and all three control methods work through an iframe.
            page.evaluate("""async () => {
              const {FTGSEmbed} = await import('./embed.js');
              const frame = document.createElement('iframe'); frame.allow = 'autoplay'; document.body.append(frame);
              window.embedded = new FTGSEmbed(frame, {src:new URL('./?autoplay=0&speed=0.5&loop=1&muted=1&volume=0.3',location.href)});
              await embedded.ready;
              const state = await embedded.load(bundle);
              if (state.playbackRate !== 0.5 || !state.loop || !state.audio.muted || state.audio.volume !== 0.3) throw new Error('Query precedence');
              await embedded.setPlaybackRate(2); await embedded.setVolume(0.7); await embedded.setMuted(false);
              const changed = await embedded.getState();
              if (changed.playbackRate !== 2 || changed.audio.volume !== 0.7 || changed.audio.muted) throw new Error('Iframe audio commands');
              await embedded.seek(0.25); await embedded.play(); await embedded.pause();
              embedded.destroy(); frame.remove();
            }""")
            # Audio freezes with the scene on XR tracking loss and resumes on a tracked frame.
            page.add_init_script(path=root / "tests/xr-mock.js")
            page.goto(url + "host.html")
            page.evaluate("""async () => {
              const {FTGSPlayer} = await import('./player.js?v=6');
              window.player = new FTGSPlayer(document.querySelector('#scene'), {autoplay:false, muted:true});
              await player.load('./audio-demo.tsog');
              document.querySelector('#start').onclick = async () => { player.play(); await player.enterAR(); navigator.xr.session.step(); };
            }""")
            page.locator("#start").click()
            page.wait_for_function("player.state.audio.status === 'playing'")
            page.evaluate("navigator.xr.session.step({tracked:false}); window.frozen = player.time")
            page.wait_for_timeout(100)
            assert page.evaluate("audioElements[0].paused && player.time === frozen")
            page.evaluate("navigator.xr.session.step()")
            page.wait_for_function("!audioElements[0].paused")
            page.evaluate("player.destroy()")
            assert page.evaluate("audioURLs.size === 0")
            assert not errors, errors
            browser.close()
        print("PASS: synthetic audio, metadata, autoplay denial/retry, timing, speed, seek, loop/end, mute/volume, mobile layout, canvas/iframe controls, reload/teardown and XR tracking loss")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
