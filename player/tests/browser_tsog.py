"""TSOG interoperability and playback, using fixtures made by the original encoder."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import threading

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Existing Chromium executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    prefix = "/ftgs-player/"

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if not self.path.startswith(prefix):
                return self.send_error(404)
            self.path = "/" + self.path[len(prefix):]
            return super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}{prefix}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True, args=[
                "--no-sandbox", "--use-angle=swiftshader", "--enable-webgl",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            page = browser.new_page(viewport={"width": 960, "height": 640})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto(url + "?autoplay=0")
            result = page.evaluate("""async () => {
              const check = (ok, message) => { if (!ok) throw new Error(message); };
              const {openZip} = await import('./zip.js');
              const {AttributeImages} = await import('./webp.js');
              const {readModel} = await import('./model.js');
              const {tsogRow} = await import('./tests/tsog-source.mjs');
              const {covarianceFromQuaternion} = await import('./ftgs.js');
              const {validateTSOGPackage} = await import('./tsog-validate.js');
              const {packageTSOG} = await import('./tsog-package.js');
              const path = './tests/fixtures/tsog/';
              const hashes = await (await fetch(path + 'image-hashes.json')).json();
              let imageCount = 0;
              const maximum = {};
              const near = (key, actual, expected, tolerance) => {
                const error = Math.abs(actual - expected);
                maximum[key] = Math.max(maximum[key] || 0, error);
                check(error <= tolerance, `${key}: ${actual} != ${expected}, tolerance ${tolerance}`);
              };
              for (const [name, entries] of Object.entries(hashes)) {
                const blob = await (await fetch(path + name)).blob();
                const archive = await openZip(blob), images = new AttributeImages();
                try {
                  const report = await validateTSOGPackage(await packageTSOG(blob), {
                    requireProfile: true,
                    decodeImage: (bytes, signal) => images.decode(bytes, signal),
                  });
                  check(report.level === 'attributes' && report.count === 256 && !report.legacy,
                    name + ' exhaustive profile validation');
                  for (const [file, expected] of Object.entries(entries)) {
                    const image = await images.decode(await archive.read(file));
                    check(image.width === expected.width && image.height === expected.height, file + ' dimensions');
                    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', image.rgba))]
                      .map(x => x.toString(16).padStart(2, '0')).join('');
                    check(hash === expected.sha256, name + '/' + file + ' exact attribute bytes');
                    imageCount++;
                  }
                } finally { images.destroy(); }
                const model = await readModel(blob, {maxPoints: Infinity});
                check(model.count === 256 && model.format === 'tsog', name + ' detected format/count');
                const discrete = name === 'discrete.tsog', stationary = name === 'static.tsog';
                check(model.timelineMode === (discrete ? 1 : stationary ? 2 : 0), 'timeline mode');
                check(model.degree === (stationary ? 0 : 3), 'SH degree');
                for (let i = 0; i < model.count; i++) {
                  const row = tsogRow(i, discrete), o = i * 4;
                  for (let a = 0; a < 3; a++) {
                    near('position', model.positionTime[o+a], row[['x','y','z'][a]], 0.0001);
                    near('motion', model.velocityDuration[o+a], discrete || stationary ? 0 : row['motion_'+a], name === 'continuous8.tsog' ? 0.004 : 0.00002);
                    near('DC', model.sh[i*model.coefficients*4+a], row['f_dc_'+a], 0.02);
                  }
                  const cov = covarianceFromQuaternion([0,1,2,3].map(a => row['rot_'+a]), [0,1,2].map(a => row['scale_'+a]));
                  for (let a = 0; a < 6; a++) near('covariance', a < 4 ? model.covarianceA[o+a] : model.covarianceB[o+a-4], cov[a], 0.00003);
                  near('opacity', model.alpha[i], 1 / (1 + Math.exp(-row.opacity)), 1/255);
                  near('center', model.positionTime[o+3], stationary ? 0 : row.t, 0.00002);
                  near('duration', model.velocityDuration[o+3], discrete || stationary ? 1 : row.t_scale, 0.00001);
                  if (!stationary) for (let c = 0; c < 15; c++) for (let a = 0; a < 3; a++)
                    near('SH', model.sh[(i*16+c+1)*4+a], row['f_rest_'+(a*15+c)], 0.003);
                }
                const sampled = await readModel(blob, {maxPoints: 7});
                check(sampled.count === 7 && sampled.sourceCount === 256, 'point limit');
                for (let i = 0; i < 7; i++) near('sample', sampled.positionTime[i*4], model.positionTime[Math.floor(i*256/7)*4], 0);
                const controller = new AbortController();
                let cancelled = false;
                try { await readModel(blob, {signal: controller.signal, onProgress: () => controller.abort()}); }
                catch (error) { cancelled = error.name === 'AbortError'; }
                check(cancelled, 'cancellation during decoding');
              }
              return {imageCount, maximum};
            }""")
            print("Exact WebP byte checks and source-attribute comparisons:", result)
            page.evaluate("""async () => {
              const {FTGSPlayer} = await import('./player.js?v=6');
              const canvas = document.createElement('canvas'); canvas.id = 'test-canvas';
              canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:10';
              document.body.append(canvas);
              window.testPlayer = new FTGSPlayer(canvas, {autoplay:false, loop:false, fps:2, frames:999});
              await testPlayer.load('./tests/fixtures/tsog/discrete.tsog');
            }""")
            state = page.evaluate("testPlayer.state")
            assert (state["format"], state["timeline"], state["nFrames"], state["duration"]) == ("tsog", "discrete", 4, 2)
            frames = []
            for t, expected_frame in [(0, 0), (0.25, 1), (0.5, 2), (0.75, 3), (1, 3)]:
                page.evaluate("t => testPlayer.seek(t)", t)
                page.wait_for_timeout(200)
                assert page.evaluate("testPlayer.state.frameIndex") == expected_frame
                uniforms = page.locator("#test-canvas").evaluate("""c => {
                  const gl = c.getContext('webgl2'), program = gl.getParameter(gl.CURRENT_PROGRAM);
                  return ['time','timelineMode'].map(n => gl.getUniform(program,gl.getUniformLocation(program,n)));
                }""")
                assert uniforms == [expected_frame, 1], uniforms
                frame = Image.open(BytesIO(page.locator("#test-canvas").screenshot())).convert("RGB")
                assert sum(ImageStat.Stat(frame).stddev) > 5, "Discrete frame is blank"
                frames.append(frame)
            assert sum(ImageStat.Stat(ImageChops.difference(frames[0], frames[2])).mean) > 2
            assert sum(ImageStat.Stat(ImageChops.difference(frames[3], frames[4])).mean) < 0.01
            page.evaluate("testPlayer.seek(0.76); testPlayer.play()")
            page.wait_for_timeout(150)
            assert page.evaluate("testPlayer.playing && testPlayer.frameIndex === 3"), "Last frame must have a full frame interval"
            page.wait_for_function("!testPlayer.playing && testPlayer.time === 1")
            page.evaluate("testPlayer.destroy(); document.querySelector('#test-canvas').remove()")
            # The shell uses native bundle FPS and exposes all four frames when stepping.
            page.goto(url + "?src=tests/fixtures/tsog/discrete.tsog&autoplay=0")
            page.wait_for_function("document.querySelector('#viewer').dataset.loaded === 'true'")
            page.locator("#canvas").focus()
            for frame in range(1, 5):
                assert page.locator("#timeline").get_attribute("aria-valuetext") == f"Frame {frame} of 4"
                page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowLeft")
            assert page.locator("#timeline").get_attribute("aria-valuetext") == "Frame 3 of 4"
            # A real drag/drop loads the packaged extension through the same API.
            transfer = page.evaluate_handle("""async () => {
              const blob = await (await fetch('./tests/fixtures/tsog/continuous16.tsog')).blob();
              const transfer = new DataTransfer(); transfer.items.add(new File([blob], 'animation.tsog'));
              return transfer;
            }""")
            page.dispatch_event("#viewer", "drop", {"dataTransfer": transfer})
            page.wait_for_function("document.title.startsWith('animation.tsog') && document.querySelector('#viewer').dataset.state === 'ready'")
            transfer.dispose()
            continuous = []
            for t in [0.2, 0.8]:
                page.locator("#timeline").evaluate("(el,t) => { el.value=t; el.dispatchEvent(new Event('input')); }", t)
                page.wait_for_timeout(250)
                continuous.append(Image.open(BytesIO(page.screenshot(clip={"x":150,"y":100,"width":660,"height":400}))).convert("RGB"))
            assert sum(ImageStat.Stat(continuous[0]).stddev) > 5, "Continuous scene is blank"
            assert sum(ImageStat.Stat(ImageChops.difference(*continuous)).mean) > 2, "Continuous TSOG did not animate"
            # Iframe API, metadata FPS, static loading, and a malformed bundle followed by recovery.
            page.evaluate("""async () => {
              const {FTGSEmbed} = await import('./embed.js');
              const iframe = document.createElement('iframe'); document.body.append(iframe);
              window.embedded = new FTGSEmbed(iframe, {src: new URL('./?autoplay=0', location.href)});
              await embedded.ready;
              const state = await embedded.load(new URL('./tests/fixtures/tsog/discrete.tsog', location.href));
              if (state.fps !== 24 || state.duration !== 4/24) throw new Error('Native TSOG frame rate');
              await embedded.seek(0.5);
              if ((await embedded.getState()).frameIndex !== 2) throw new Error('Iframe discrete seek');
              await embedded.load(new URL('./tests/fixtures/tsog/static.tsog', location.href));
              const stationary = await embedded.play();
              if (stationary.playing || stationary.timeline !== 'static') throw new Error('Static TSOG must stay paused');
              const bytes = new Uint8Array(await (await fetch('./tests/fixtures/tsog/discrete.tsog')).arrayBuffer());
              let failed = false;
              try { await embedded.load(new Blob([bytes.slice(0, -100)])); } catch { failed = true; }
              if (!failed) throw new Error('Truncated TSOG accepted');
              await embedded.load(new URL('./tests/fixtures/tsog/continuous8.tsog', location.href));
              if ((await embedded.getState()).timeline !== 'continuous') throw new Error('Failed recovery');
              embedded.destroy(); iframe.remove();
            }""")
            assert not errors, errors
            browser.close()
        print("PASS: TSOG exact image bytes, continuous/discrete/static decoding, SH3, motion8/16, cancellation, sampling, rendered frames, timing, URL/drop/iframe loading and recovery")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
