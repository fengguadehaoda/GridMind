// Probe SVG sizes via Edge CDP
const { spawn } = require('child_process');
const http = require('http');
const WebSocket = require('ws');

const edgePath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

const child = spawn(edgePath, [
  '--headless=new', '--disable-gpu', '--no-sandbox',
  '--disable-extensions',
  '--disable-component-extensions-with-background-pages',
  '--remote-debugging-port=9228', 'http://localhost:5173/'
], { detached: true, stdio: 'ignore' });
child.unref();

const wait = (ms) => new Promise(r => setTimeout(r, ms));

let nextId = 1;
const sendEval = (ws, expr) => new Promise((res) => {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression: expr, returnByValue: true } }));
  const onMsg = (msg) => {
    const m = JSON.parse(msg);
    if (m.id === id) {
      ws.off('message', onMsg);
      if (m.result.exceptionDetails) {
        res({ error: m.result.exceptionDetails.text });
      } else {
        res(m.result.result.value);
      }
    }
  };
  ws.on('message', onMsg);
});

(async () => {
  await wait(4000);
  const tabs = await new Promise((res, rej) => {
    http.get('http://127.0.0.1:9228/json', (r) => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => res(JSON.parse(d)));
    }).on('error', rej);
  });
  if (!tabs[0]) { console.log('No tabs'); process.exit(1); }
  console.log('Connecting to:', tabs[0].url);
  const ws = new WebSocket(tabs[0].webSocketDebuggerUrl);
  await new Promise(r => ws.on('open', r));
  console.log('Connected');

  // Wait for app to mount
  let count = 0;
  for (let i = 0; i < 30; i++) {
    count = await sendEval(ws, 'document.querySelectorAll("svg").length');
    if (count > 5) break;
    await wait(500);
  }
  console.log('SVG count:', count);

  if (count === 0) {
    console.log('Page did not render');
    const url = await sendEval(ws, 'window.location.href');
    const body = await sendEval(ws, 'document.body.innerHTML.length');
    console.log('URL:', url, 'body length:', body);
    process.exit(1);
  }

  const expr = [
    '(function() {',
    '  const svgs = document.querySelectorAll("svg");',
    '  return Array.from(svgs).map((s, i) => {',
    '    const r = s.getBoundingClientRect();',
    '    const p = s.querySelector("path");',
    '    return {',
    '      i,',
    '      vb: s.getAttribute("viewBox"),',
    '      w: s.getAttribute("width"),',
    '      h: s.getAttribute("height"),',
    '      cw: Math.round(r.width),',
    '      ch: Math.round(r.height),',
    '      x: Math.round(r.left),',
    '      y: Math.round(r.top),',
    '      p: s.parentElement ? s.parentElement.tagName + "." + (s.parentElement.getAttribute("class") || "").substring(0, 50) : "",',
    '      d: p ? (p.getAttribute("d") || "").substring(0, 50) : ""',
    '    };',
    '  });',
    '})()'
  ].join('\n');

  const svgs = await sendEval(ws, expr);
  console.log('Total SVGs:', svgs.length);
  svgs.forEach(s => {
    console.log('[' + s.i + '] vb=' + s.vb + ' attr=' + s.w + 'x' + s.h + ' computed=' + s.cw + 'x' + s.ch + ' @(' + s.x + ',' + s.y + ')');
    console.log('     parent=' + s.p);
    console.log('     d=' + s.d);
  });

  ws.close();
  process.exit(0);
})().catch(e => { console.log('Error:', e.message); process.exit(1); });
