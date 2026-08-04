// Probe SVG sizes via Edge CDP
const { spawn } = require('child_process');
const http = require('http');
const WebSocket = require('ws');

const edgePath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

const child = spawn(edgePath, [
  '--headless=new', '--disable-gpu', '--no-sandbox',
  '--remote-debugging-port=9226', 'http://localhost:5173/'
], { detached: true, stdio: 'ignore' });
child.unref();

const wait = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  await wait(4000);
  const tabs = await new Promise((res, rej) => {
    http.get('http://127.0.0.1:9226/json', (r) => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => res(JSON.parse(d)));
    }).on('error', rej);
  });
  if (!tabs[0]) { console.log('No tabs'); process.exit(1); }
  console.log('Connecting...');
  const ws = new WebSocket(tabs[0].webSocketDebuggerUrl);
  await new Promise(r => ws.on('open', r));
  console.log('Connected');

  // Enable page events
  ws.send(JSON.stringify({ id: 1, method: 'Page.enable' }));
  await wait(500);

  // Wait for load event
  await new Promise((res) => {
    const onMsg = (msg) => {
      const m = JSON.parse(msg);
      if (m.method === 'Page.loadEventFired') {
        console.log('Page loaded');
        ws.off('message', onMsg);
        res();
      }
    };
    ws.on('message', onMsg);
  });

  // Wait for Vue to mount
  for (let i = 0; i < 20; i++) {
    ws.send(JSON.stringify({
      id: 100 + i,
      method: 'Runtime.evaluate',
      params: { expression: 'document.querySelectorAll("svg").length', returnByValue: true }
    }));
    await wait(500);
  }

  // Get SVG count
  const svgCount = await new Promise((res) => {
    ws.send(JSON.stringify({
      id: 200,
      method: 'Runtime.evaluate',
      params: { expression: 'document.querySelectorAll("svg").length', returnByValue: true }
    }));
    const onMsg = (msg) => {
      const m = JSON.parse(msg);
      if (m.id === 200) {
        ws.off('message', onMsg);
        res(m.result.result.value);
      }
    };
    ws.on('message', onMsg);
  });
  console.log('SVG count after wait:', svgCount);
  if (svgCount === 0) {
    console.log('Page did not render. Exiting.');
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

  ws.send(JSON.stringify({ id: 2, method: 'Runtime.evaluate', params: { expression: expr, returnByValue: true } }));

  await new Promise(res => {
    const onMsg = (msg) => {
      const m = JSON.parse(msg);
      if (m.id === 2) {
        ws.off('message', onMsg);
        const svgs = m.result.result.value;
        console.log('Total SVGs:', svgs.length);
        svgs.forEach(s => {
          console.log('[' + s.i + '] vb=' + s.vb + ' attr=' + s.w + 'x' + s.h + ' computed=' + s.cw + 'x' + s.ch + ' @(' + s.x + ',' + s.y + ')');
          console.log('     parent=' + s.p);
          console.log('     d=' + s.d);
        });
        res();
      }
    };
    ws.on('message', onMsg);
  });
  ws.close();
  process.exit(0);
})().catch(e => { console.log('Error:', e.message); process.exit(1); });
