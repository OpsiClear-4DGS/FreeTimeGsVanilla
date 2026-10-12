"""Profile the full-quality player decoder with a resident TSOG Blob in Chromium."""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
import threading
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--runs', type=int, default=3)
parser.add_argument('--capture', action='store_true', help='Hash every logical coefficient and two rendered frames')
parser.add_argument('--compare', type=Path, help='Require exact attribute and frame hashes from a captured baseline')
parser.add_argument('--browser', help='Existing Chromium executable')
parser.add_argument('--example', default='examples/assets/jensen-2m.json', help='Example manifest path or URL, relative to the served project root')
parser.add_argument('--main-thread', action='store_true', help='Benchmark the direct decoder for comparison with worker loading')
args = parser.parse_args()
if args.runs < 1: parser.error('--runs must be positive')
if args.compare: args.capture = True
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == '/__benchmark__':
            body = b'<!doctype html><html><head><style>body{margin:0}canvas{display:block;width:640px;height:480px}</style></head><body><canvas></canvas></body></html>'
            self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body)
        else: super().do_GET()
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(args.root)))
threading.Thread(target=server.serve_forever, daemon=True).start()
script = r'''async ({capture, example, mainThread}) => {
  const {readModel} = await import('/src/model.js');
  const {readModelInWorker} = await import('/src/decode.js');
  const {AttributeImages} = await import('/src/webp.js');
  const {SplatRenderer} = await import('/src/renderer.js');
  const {OrbitCamera} = await import('/src/camera.js');
  const {createSorter} = await import('/src/sort.js');
  const metadataURL = new URL(example, location.origin + '/');
  const metadata = await (await fetch(metadataURL)).json();
  let start = performance.now();
  const blob = await (await fetch(new URL(metadata.file, metadataURL))).blob();
  const downloadMs = performance.now() - start;
  const images = [];
  const original = AttributeImages.prototype.decode;
  AttributeImages.prototype.decode = async function (...args) {
    const start = performance.now();
    const result = await original.apply(this, args);
    images.push({ms:performance.now()-start, width:result.width, height:result.height});
    return result;
  };
  let heartbeat = performance.now(), maxHeartbeatGapMs = 0;
  const timer = setInterval(() => {
    const now = performance.now(); maxHeartbeatGapMs = Math.max(maxHeartbeatGapMs, now-heartbeat); heartbeat=now;
  }, 16);
  const stages = [];
  start = performance.now();
  let previous = start;
  const data = await (mainThread ? readModel : readModelInWorker)(blob, {maxPoints:Infinity, onProgress(progress) {
    const now = performance.now(); stages.push({progress,ms:now-previous});previous=now;
  }});
  const decodeMs = performance.now()-start;
  await new Promise(resolve => setTimeout(resolve, 0));
  clearInterval(timer);
  const cpuBytes = ['positionTime','velocityDuration','covarianceA','covarianceB','sh','alpha'].reduce((n,key)=>n+data[key].byteLength,0);
  const canvas = document.querySelector('canvas');
  const renderer = new SplatRenderer(canvas);
  const gpuBytes = [];
  const makeTexture = renderer.makeTexture.bind(renderer);
  renderer.makeTexture = values => {
    const texture = makeTexture(values);
    gpuBytes.push(values.byteLength);
    return texture;
  };
  start = performance.now();
  renderer.setModel(data);
  renderer.gl.finish();
  const uploadMs = performance.now()-start;
  const camera = new OrbitCamera(canvas, () => {});
  camera.fit(data.bounds); camera.restore(metadata.view);
  start = performance.now();
  const sort = createSorter(data);
  const sortPrepareMs = performance.now()-start;
  start = performance.now();
  const order = sort(0, camera.snapshot().view);
  const sortMs = performance.now()-start;
  start = performance.now();
  renderer.draw(order, camera.snapshot(), 0, data.degree);
  renderer.gl.finish();
  const drawMs = performance.now()-start;
  const warmSortTimes=[];
  for(let i=0;i<14;i++) {
    start=performance.now();
    sort((i%7)/6,camera.snapshot().view);
    warmSortTimes.push(performance.now()-start);
  }
  const warmSortMs=warmSortTimes.toSorted((a,b)=>a-b)[7];
  const result = {decoder:mainThread?'direct':'worker', downloadMs, decodeMs, uploadMs, sortPrepareMs, sortMs, warmSortMs, drawMs, maxHeartbeatGapMs, cpuBytes, decodeUploadMs:decodeMs+uploadMs, visiblePoints:order.length, gpuUploadBytes:gpuBytes.reduce((a,b)=>a+b,0), stages, images, count:data.count, coefficients:data.coefficients, harmonicPointCount:data.harmonicPointCount ?? 0};
  if (capture) {
    const hash = async buffer => [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(v=>v.toString(16).padStart(2,'0')).join('');
    result.hashes = {};
    for (const key of ['positionTime','velocityDuration','covarianceA','covarianceB','alpha']) result.hashes[key] = await hash(data[key]);
    // Compare logical coefficients even when the GPU uses a shared palette.
    const sh = new Float32Array(data.count * data.coefficients * 4);
    for (let i=0; i<data.count; i++) {
      for(let c=0; c<data.coefficients; c++) {
        const from = data.harmonicPointCount ? (c ? data.harmonicPointCount + data.sh[i*4+3]*(data.coefficients-1)+c-1 : i) * 4 : (i*data.coefficients+c)*4;
        for(let a=0;a<3;a++) sh[(i*data.coefficients+c)*4+a]=data.sh[from+a];
      }
    }
    result.hashes.sh = await hash(sh);
    result.frames = [];
    for (const time of [.15,.7]) {
      renderer.draw(sort(time,camera.snapshot().view), camera.snapshot(), time, data.degree);
      const rgba = new Uint8Array(canvas.width*canvas.height*4);
      renderer.gl.readPixels(0,0,canvas.width,canvas.height,renderer.gl.RGBA,renderer.gl.UNSIGNED_BYTE,rgba);
      result.frames.push({time, sha256:await hash(rgba)});
    }
  }
  camera.destroy(); renderer.destroy();
  return result;
}'''
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, headless=True, args=['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader','--enable-webgl','--ignore-gpu-blocklist','--disable-dev-shm-usage'])
        runs=[]
        for i in range(args.runs):
            page=browser.new_page(viewport={'width':640,'height':480})
            page.goto(f'http://127.0.0.1:{server.server_port}/__benchmark__')
            result=page.evaluate(script, {'capture':args.capture and i==0, 'example':args.example, 'mainThread':args.main_thread})
            runs.append(result)
            print(json.dumps({'run':i+1, **{k:v for k,v in result.items() if k not in ('stages','images','hashes','frames')}}), flush=True)
            page.close()
        summary={key:median(r[key] for r in runs) for key in ('downloadMs','decodeMs','uploadMs','sortPrepareMs','sortMs','warmSortMs','drawMs','maxHeartbeatGapMs','cpuBytes','gpuUploadBytes','decodeUploadMs')}
        if args.compare:
            baseline=json.loads(args.compare.read_text())['runs'][0]
            for key in ('count','coefficients','hashes','frames'):
                assert runs[0][key] == baseline[key], f'Baseline mismatch: {key}'
            print('Every logical attribute and both rendered frames match the baseline exactly.', flush=True)
        args.output.write_text(json.dumps({'environment':'Chromium headless, SwiftShader, local HTTP, resident TSOG Blob, 640x480, full Slop Example (2M Gaussians)','median':summary,'runs':runs},indent=2)+'\n')
        print(json.dumps({'median':summary}), flush=True)
        browser.close()
finally: server.shutdown(); server.server_close()
