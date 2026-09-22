import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer, request as proxyRequest } from 'node:http';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { spawn } from 'node:child_process';

const musicRoot = resolve(process.env.MUSIC_PRACTICE_SITE_ROOT || join(process.cwd(), 'site'));
const port = Number(process.env.MUSIC_PRACTICE_PORT || 8790);
const host = process.env.MUSIC_PRACTICE_HOST || '127.0.0.1';
const plannerRoot = resolve(process.env.PRACTICE_PLANNER_SITE_ROOT || join(musicRoot, 'planner'));
const plannerPort = Number(process.env.PRACTICE_PORT || 8977);
const legacyApiPort = Number(process.env.MUSIC_LEGACY_API_PORT || 8787);
const plannerApi = new Set(['/api/health', '/api/meta', '/api/file', '/api/year', '/api/sessions', '/api/sessions/refresh', '/api/sessions/action', '/api/sessions/adjust', '/api/preferences', '/api/observations', '/api/chat', '/api/notebook', '/api/notebook/note', '/api/notebook/task', '/api/notebook/stage']);
const plannerAssets = new Set(['/', '/index.html', '/app.js', '/notebook-ui.js', '/app.css', '/manifest.webmanifest', '/icon.svg', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png', '/sw.js']);
let plannerChild = null;
let plannerStarting = false;

// The existing gateway remains on 8790; the planner's standard local backend
// is supervised in place. Other APIs keep their existing 8787 destination.
async function ensurePlanner() {
  const script = process.env.PRACTICE_SERVER_PATH;
  const python = process.env.PRACTICE_PYTHON;
  if (!script || !python || plannerChild || plannerStarting) return;
  plannerStarting = true;
  try {
    try {
      const response = await fetch(`http://127.0.0.1:${plannerPort}/api/health`, { signal: AbortSignal.timeout(2000) });
      const info = await response.json();
      if (info.app !== 'practice-room') console.error('Planner port belongs to another service; leaving it untouched.');
      return;
    } catch (error) {
      if (error?.cause?.code !== 'ECONNREFUSED') return;
    }
    const child = spawn(python, [script, '--no-browser'], {
      cwd: resolve(script, '..'), windowsHide: true,
      env: { ...process.env, BROWSER_NONE: '1', PRACTICE_SKIP_GIT_PULL: '1' },
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    plannerChild = child;
    const clearChild = () => { if (plannerChild === child) plannerChild = null; };
    child.on('error', error => { console.error('Planner launch failed:', error.message); clearChild(); });
    child.on('exit', code => { console.error('Planner exited:', code); clearChild(); });
    child.on('close', clearChild);
  } finally {
    plannerStarting = false;
  }
}

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

function resolveRequest(pathname, root) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const relative = normalize(decoded.replace(/^\/+/, '')).replace(/^(\.\.[/\\])+/, '');
  const candidates = [
    join(root, relative || 'index.html'),
    join(root, `${relative}.html`),
    join(root, relative, 'index.html'),
  ];
  for (const candidate of candidates) {
    const absolute = resolve(candidate);
    if (absolute.startsWith(root + sep) && existsSync(absolute) && statSync(absolute).isFile()) {
      return absolute;
    }
  }
  return join(root, 'index.html');
}

function proxyTo(request, response, { port, path, service }) {
  const upstream = proxyRequest(
    {
      hostname: '127.0.0.1',
      port,
      path,
      method: request.method,
      headers: { ...request.headers, host: `127.0.0.1:${port}` },
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.on('error', (error) => {
    if (!response.headersSent) response.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
    response.end(`${service} unavailable: ${error.message}`);
  });
  response.on('close', () => {
    if (!response.writableEnded) upstream.destroy();
  });
  request.pipe(upstream);
}

const server = createServer((request, response) => {
  const requestHost = (request.headers.host || '').split(':')[0].toLowerCase();
  if (!['127.0.0.1', 'localhost', 'lox-pc.tail89d19b.ts.net'].includes(requestHost)) {
    response.writeHead(403); response.end('Untrusted host'); return;
  }
  if (request.url === '/healthz') {
    response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
    response.end(JSON.stringify({ ok: true, app: 'music-practice', pid: process.pid }));
    return;
  }

  if (request.url === '/music' || request.url === '/music/') {
    response.writeHead(302, { location: '/home' });
    response.end();
    return;
  }

  if ((request.url || '').startsWith('/stockfish/')) {
    const path = (request.url || '/').slice('/stockfish'.length) || '/';
    proxyTo(request, response, { port: 38419, path, service: 'Stockfish' });
    return;
  }

  if ((request.url || '').startsWith('/api/')) {
    const apiPath = new URL(request.url, 'http://localhost').pathname;
    const isPlanner = plannerApi.has(apiPath);
    proxyTo(request, response, { port: isPlanner ? plannerPort : legacyApiPort, path: request.url, service: isPlanner ? 'Practice Room' : 'En Croissant' });
    return;
  }

  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { allow: 'GET, HEAD' });
    response.end();
    return;
  }

  const pathname = new URL(request.url || '/', 'http://localhost').pathname;
  const file = resolveRequest(pathname, plannerAssets.has(pathname) && existsSync(join(plannerRoot, 'index.html')) ? plannerRoot : musicRoot);
  if (!file || !existsSync(file)) {
    response.writeHead(404);
    response.end('Not found');
    return;
  }

  const stats = statSync(file);
  const extension = extname(file).toLowerCase();
  const immutable = file.includes(`${join(musicRoot, '_expo', 'static')}`);
  response.writeHead(200, {
    'content-type': contentTypes[extension] || 'application/octet-stream',
    'content-length': stats.size,
    'cache-control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
    'x-content-type-options': 'nosniff',
  });
  if (request.method === 'HEAD') response.end();
  else createReadStream(file).pipe(response);
});

server.listen(port, host, () => {
  console.log(`Private app gateway listening at http://${host}:${port}`);
  ensurePlanner();
  setInterval(ensurePlanner, 15000).unref();
});
