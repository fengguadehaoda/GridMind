// Use Edge CDP to probe all SVG computed sizes
const { spawn } = require('child_process');
const http = require('http');
const WebSocket = require('ws');

const edgePath = '"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"';

// Launch Edge with remote debugging
const child = spawn(edgePath, [
  '--headless=new', '--disable-gpu', '--no-sandbox',
  '--remote-debugging-port=9224', 'http://localhost:5173/'
], { detached: true, stdio: 'ignore' });
child.unref();

const wait = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  await wait(3000);

  // Get tab info
  const tabs = await new Promise((res, rej) => {
    http.get('http://127.0.0.1:9224/json', (r) => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => res(JSON.parse(d)));
    }).on('error', rej);
  });

  if (!tabs[0]) { console.log('No tabs'); process.exit(1); }
  console.log('Connecting to:', tabs[0].webSocketDebuggerUrl);
  const ws = new WebSocket(tabs[0].webSocketDebuggerUrl);

  await new Promise(r => ws.on('open', r));

  // Wait for page to fully load
  await wait(5000);

  // Enable Runtime domain
  ws.send(JSON.stringify({ id: 1, method: 'Runtime.enable' }));
  await wait(200);

  // Execute JS
  const expr = `
    (function() {
      const svgs = document.querySelectorAll('svg');
      return Array.from(svgs).map((s, i) => {
        const r = s.getBoundingClientRect();
        return {
          i,
          vb: s.getAttribute('viewBox'),
          attrW: s.getAttribute('width'),
          attrH: s.getAttribute('height'),
          cw: r.width, ch: r.height,
          x: r.left, y: r.top,
          cls: (s.getAttribute('class') || '').substring(0, 60),
          ptag: s.parentElement ? s.parentElement.tagName : '',
          pcls: s.parentElement ? (s.parentElement.getAttribute('class') || '').substring(0, 60) : '',
          pathSnip: (s.querySelector('path')?.getAttribute('d') || '').substring(0, 50),
        };
      });
    })()
  `;
  ws.send(JSON.stringify({
    id: 2,
    method: 'Runtime.evaluate',
    params: { expression: expr, returnByValue: true }
  }));

  await new Promise(res => {
    const onMsg = (msg) => {
      const m = JSON.parse(msg);
      if (m.id === 2) {
        ws.off('message', onMsg);
        const svgs = m.result.result.value;
        console.log('Total SVGs:', svgs.length);
        console.log('');
        svgs.forEach(s => {
          console.log(`[${s.i}] viewBox=${s.vb} | attr=${s.attrW||'-'}x${s.attrH||'-'} | computed=${Math.round(s.cw)}x${Math.round(s.ch)} @(${Math.round(s.x)},${Math.round(s.y)})`);
          console.log(`     parent: <${s.ptag.toLowerCase()} class="${s.pcls}">`);
          console.log(`     path: ${s.pathSnip}...`);
          console.log('');
        });
        res();
      }
    };
    ws.on('message', onMsg);
  });

  ws.close();
  process.exit(0);
})();
