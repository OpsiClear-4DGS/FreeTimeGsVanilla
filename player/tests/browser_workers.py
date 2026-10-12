"""Check byte-exact worker decoding, fallback, cancellation and resource ownership."""
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

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def do_GET(self):
            if self.path == "/worker-check.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b'<!doctype html><link rel="icon" href="data:,"><title>Worker checks</title>')
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True, args=[
                "--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                "--enable-webgl", "--ignore-gpu-blocklist", "--disable-dev-shm-usage",
            ])
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/worker-check.html")
            result = page.evaluate("""async () => {
              const check = (ok, message) => { if (!ok) throw new Error(message); };
              const {readModel} = await import('/src/model.js');
              const {readModelInWorker} = await import('/src/decode.js');
              const {packageTSOG} = await import('/src/tsog-package.js');
              const {toneFile} = await import('/tests/audio-fixture.mjs');
              const {demoFile} = await import('/site/demo.js');
              const NativeWorker = Worker, live = new Set(), messages = [];
              class TrackedWorker extends NativeWorker {
                constructor(...args) {
                  super(...args); live.add(this);
                  this.addEventListener('message', ({data}) => messages.push(data.unsupported ? 'unsupported' : data.type));
                }
                terminate() { live.delete(this); super.terminate(); }
              }
              globalThis.Worker = TrackedWorker;
              const bytesEqual = (a,b) => {
                const left = new Uint8Array(a.buffer,a.byteOffset,a.byteLength);
                const right = new Uint8Array(b.buffer,b.byteOffset,b.byteLength);
                return left.length === right.length && left.every((v,i) => v===right[i]);
              };
              const equal = async (a,b) => {
                for (const key of ['positionTime','velocityDuration','covarianceA','covarianceB','sh','alpha'])
                  check(bytesEqual(a[key],b[key]), 'Worker changes '+key);
                for (const key of ['format','timelineMode','count','sourceCount','degree','coefficients','harmonicPointCount','nFrames','fps','playback','useVelocity','opacityFloor','bounds'])
                  check(JSON.stringify(a[key])===JSON.stringify(b[key]), 'Worker changes '+key);
                check(Boolean(a.audio)===Boolean(b.audio), 'Worker loses audio');
                if(a.audio) check(bytesEqual(new Uint8Array(await a.audio.blob.arrayBuffer()),new Uint8Array(await b.audio.blob.arrayBuffer())), 'Worker changes audio bytes');
              };
              const files = ['static','discrete','continuous8','continuous16'];
              const fixtures = await Promise.all(files.map(async name => (await fetch('/tests/fixtures/tsog/'+name+'.tsog')).blob()));
              fixtures.push(await packageTSOG(fixtures[3], {audio:toneFile(),playback:{duration:4,fps:24}}));
              fixtures.push(demoFile());
              let comparisons=0;
              for(const blob of fixtures) for(const maxPoints of [Infinity,7]) {
                const expected=await readModel(blob,{maxPoints}), progress=[];
                const decoded=await readModelInWorker(blob,{maxPoints,onProgress:p=>progress.push(p)});
                await equal(decoded,expected); comparisons++;
                check(progress.at(-1)===1 && progress.every((p,i)=>!i || p>=progress[i-1]), 'Worker progress');
                check(live.size===0, 'Successful decode leaks worker');
              }
              check(messages.includes('ready') && messages.includes('decoded'), 'Decode ran only on main thread');
              const controller=new AbortController();
              let aborted=false;
              try { await readModelInWorker(fixtures[3], {signal:controller.signal,onProgress:()=>controller.abort()}); }
              catch(e) { aborted=e.name==='AbortError'; }
              check(aborted && live.size===0, 'Decode cancellation/cleanup');
              const early=new AbortController(); early.abort();
              try { await readModelInWorker(fixtures[3],{signal:early.signal}); throw Error('Early abort ignored'); }
              catch(e) { check(e.name==='AbortError','Early abort changes error'); }
              let mainSlices=0;
              const invalid=new Blob([await fixtures[3].slice(0,-10).arrayBuffer()]);
              const slice=invalid.slice.bind(invalid);
              invalid.slice=(...args)=>{mainSlices++;return slice(...args);};
              try { await readModelInWorker(invalid); throw Error('Corrupt package accepted'); }
              catch(e) { check(e.message.includes('ZIP'), 'Corrupt package changes error'); }
              check(mainSlices===1 && live.size===0, 'Corrupt package retried on main thread or leaked worker');
              const expected=await readModel(fixtures[3]);
              // Force unsupported offscreen capability without changing the main decoder.
              globalThis.Worker=class extends TrackedWorker {
                constructor() { super('data:text/javascript,self.postMessage({type:"ready",tsog:false})',{type:'module'}); }
              };
              await equal(await readModelInWorker(fixtures[3]),expected);
              check(live.size===0, 'Capability fallback leaks worker');
              // Exercise a browser that has OffscreenCanvas but no worker WebGL2.
              const workerSource=(await (await fetch('/src/decode-worker.js')).text())
                .replace('"./model.js"',JSON.stringify(new URL('/src/model.js',location.href).href));
              globalThis.Worker=class extends TrackedWorker {
                constructor() {
                  const source='OffscreenCanvas.prototype.getContext=()=>null;'+workerSource;
                  super('data:text/javascript,'+encodeURIComponent(source),{type:'module'});
                }
              };
              await equal(await readModelInWorker(fixtures[3]),expected);
              check(messages.includes('unsupported') && live.size===0, 'Worker WebGL2 fallback/cleanup failed');
              globalThis.Worker=class { constructor(){throw new DOMException('Blocked','SecurityError');} };
              await equal(await readModelInWorker(fixtures[3]),expected);
              globalThis.Worker=TrackedWorker;
              // Abort before the worker's ready event and verify immediate teardown.
              const startupAbort=new AbortController();
              const pending=readModelInWorker(fixtures[3],{signal:startupAbort.signal}).catch(e=>e.name);
              await new Promise(resolve=>setTimeout(resolve,0)); startupAbort.abort();
              check(await pending==='AbortError' && live.size===0, 'Startup cancellation leaks worker');
              globalThis.Worker=NativeWorker;
              return {comparisons, workerDecodedMessages:messages.filter(t=>t==='decoded').length, liveWorkers:live.size};
            }""")
            assert not errors, errors
            print("PASS: exact worker/direct attributes and audio, sampling, progress, corrupt-file rejection, cancellation, startup/capability fallback and cleanup:", result, flush=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
