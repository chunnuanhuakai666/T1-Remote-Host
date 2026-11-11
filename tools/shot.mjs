// 截图工具：通过 CDP 打开页面，等动画稳定后截图
// 用法: node tools/shot.mjs <url> <out.png> [waitMs] [width] [height]
import { writeFileSync } from 'node:fs';

const [, , url, out, waitMs = '1400', W = '1520', H = '960', expr = ''] = process.argv;
const base = 'http://127.0.0.1:9222';

const r = await fetch(`${base}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
const target = await r.json();
const ws = new WebSocket(target.webSocketDebuggerUrl);

let id = 0;
const pending = new Map();
const send = (method, params = {}) => new Promise((res, rej) => {
  const mid = ++id;
  pending.set(mid, { res, rej });
  ws.send(JSON.stringify({ id: mid, method, params }));
});

ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.rej(new Error(m.error.message)) : p.res(m.result);
  }
});

await new Promise((res) => ws.addEventListener('open', res));
await send('Emulation.setDeviceMetricsOverride', {
  width: +W, height: +H, deviceScaleFactor: 1, mobile: false,
});
await send('Page.enable');
await new Promise((r2) => setTimeout(r2, +waitMs));
if (expr) {
  await send('Runtime.enable');
  const res = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (res.exceptionDetails) console.error('eval error:', JSON.stringify(res.exceptionDetails.text));
  else if (res.result && res.result.value !== undefined) console.log('eval:', res.result.value);
  await new Promise((r2) => setTimeout(r2, 700));
}
const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
writeFileSync(out, Buffer.from(shot.data, 'base64'));
await fetch(`${base}/json/close/${target.id}`);
ws.close();
console.log('saved', out);
process.exit(0);
