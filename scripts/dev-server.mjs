#!/usr/bin/env node
// Local dev server: static public/ + api/*.js serverless handlers, no Vercel login.
// Usage: node scripts/dev-server.mjs  →  http://localhost:3001/dashboard.html
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = new URL('..', import.meta.url).pathname;
const PORT = process.env.PORT || 3001;
const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2',
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  try {
    if (url.pathname.startsWith('/api/')) {
      const name = url.pathname.slice(5).replace(/[^a-z0-9-]/g, '');
      const mod = await import(pathToFileURL(join(ROOT, 'api', `${name}.js`)));
      let body = '';
      for await (const chunk of req) body += chunk;
      const handler = mod.default;
      // Edge-style handler: handler(Request) -> Response (probe without calling twice)
      const webReq = new Request(url.href, {
        method: req.method,
        headers: req.headers,
        body: ['GET', 'HEAD'].includes(req.method) ? undefined : body,
      });
      const out = handler.length <= 1 ? await handler(webReq) : undefined;
      if (out instanceof Response) {
        res.writeHead(out.status, Object.fromEntries(out.headers.entries()));
        return res.end(Buffer.from(await out.arrayBuffer()));
      }
      // Node-style handler: handler(req, res)
      const shimReq = { method: req.method, query: Object.fromEntries(url.searchParams), body: body ? JSON.parse(body) : {}, headers: req.headers, url: req.url };
      const shimRes = {
        statusCode: 200,
        setHeader: (k, v) => res.setHeader(k, v),
        status(code) { this.statusCode = code; return this; },
        json(obj) { res.writeHead(this.statusCode, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); },
        send(text) { res.writeHead(this.statusCode); res.end(text); },
        end() { res.writeHead(this.statusCode); res.end(); },
      };
      return await handler(shimReq, shimRes);
    }
    // static
    let p = normalize(decodeURIComponent(url.pathname)).replace(/^([/\\])+/, '');
    if (p === '') p = 'dashboard.html';
    const file = join(ROOT, 'public', p);
    if (!file.startsWith(join(ROOT, 'public'))) { res.writeHead(403); return res.end(); }
    const data = await readFile(file);
    res.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
    res.end(data);
  } catch (e) {
    if (e.code === 'ENOENT') { res.writeHead(404); res.end('not found'); }
    else { console.error(e); res.writeHead(500); res.end(String(e.message || e)); }
  }
});
server.listen(PORT, () => console.log(`APEX dev server → http://localhost:${PORT}/dashboard.html`));
