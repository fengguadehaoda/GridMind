// Render test SVGs at 64x64 to compare with ASCII art
const { exec, spawn } = require('child_process');
const http = require('http');
const WebSocket = require('ws');
const fs = require('fs');

const edgePath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

const child = spawn(edgePath, [
  '--headless=new', '--disable-gpu', '--no-sandbox',
  '--disable-extensions',
  '--remote-debugging-port=9229', 'about:blank'
], { detached: true, stdio: 'ignore' });
child.unref();

const wait = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  await wait(4000);
  const tabs = await new Promise((res, rej) => {
    http.get('http://127.0.0.1:9229/json', (r) => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => res(JSON.parse(d)));
    }).on('error', rej);
  });
  if (!tabs[0]) { console.log('No tabs'); process.exit(1); }
  const ws = new WebSocket(tabs[0].webSocketDebuggerUrl);
  await new Promise(r => ws.on('open', r));
  await wait(500);

  // Get icon paths from EP
  const content = fs.readFileSync('F:/GridOpsAgent/web/node_modules/@element-plus/icons-vue/dist/index.js', 'utf-8');
  const matches = [...content.matchAll(/name: "([A-Z][a-zA-Z]+)",[\s\S]{0,2000}?d: "([^"]{30,400})"/g)];
  const icons = {};
  for (const m of matches) {
    if (!icons[m[1]]) icons[m[1]] = m[2];
  }
  console.log(`Loaded ${Object.keys(icons).length} icons`);

  // Get path of ChatDotRound, Comment, Postcard, etc.
  const targetIcons = ['ChatDotRound', 'Comment', 'Postcard', 'Picture', 'PictureRounded', 'Notification', 'Connection', 'Crop', 'Camera', 'CameraFilled', 'Iphone', 'Cellphone', 'Files', 'Memo', 'Document', 'DocumentAdd', 'Headset', 'Avatar', 'Service', 'UserFilled', 'User', 'DataLine', 'DataBoard', 'Cpu', 'Brush', 'Briefcase', 'Tickets', 'Ticket', 'Box', 'Goblet', 'Histogram', 'Stamp', 'Printer', 'Reading', 'Notebook', 'BellFilled', 'Bell', 'Refresh', 'Promotion', 'MagicStick', 'Monitor'];
  for (const n of targetIcons) {
    if (icons[n]) console.log(`${n}: ${icons[n].substring(0, 80)}`);
  }

  ws.close();
  process.exit(0);
})().catch(e => { console.log('Error:', e.message); process.exit(1); });
