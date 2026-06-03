// /api/quant/* — same-origin proxy to the hosted quant engine on Render.
//
// Why this exists: a browser calling the Render free-tier engine directly hits
// HTTP 503 while the dyno is asleep/waking, and Render's 503 page carries no
// CORS header — so the fetch fails outright ("Failed to fetch") and the quant
// cross-check shows OFFLINE, even though the engine answers server-to-server
// perfectly (curl gets 200 in <1s). Routing through this Vercel function makes
// the call same-origin and runs it server-side (like curl), where it succeeds.
// We also retry through the cold-start 502/503s so the first scan after the
// engine has gone idle still gets a real answer instead of a dead OFFLINE card.
//
//   GET /api/quant/health        -> engine /health
//   GET /api/quant/regime/NVDA   -> engine /regime/NVDA
//   GET /api/quant/risk/BTC%2FUSD?equity=100000 -> engine /risk/BTC%2FUSD?equity=100000
// The path + query after "/api/quant" are forwarded verbatim (encoded slashes in
// instrument ids are preserved exactly).

export const config = { maxDuration: 60 };

const ENGINE = 'https://apex-quant-engine.onrender.com';
const BUDGET_MS = 55000;           // stay safely under the 60s function ceiling
const ATTEMPT_TIMEOUT_MS = 18000;  // per-try cap; a warm engine answers in <1s
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export default async function handler(req, res) {
  // Same-origin in production, but keep this permissive for local dev tooling.
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }
  if (req.method !== 'GET') { res.status(405).json({ error: 'method not allowed' }); return; }

  const marker = '/api/quant';
  const i = req.url.indexOf(marker);
  let tail = i >= 0 ? req.url.slice(i + marker.length) : '';
  if (!tail.startsWith('/')) tail = '/' + tail;
  const target = ENGINE + tail;

  const deadline = Date.now() + BUDGET_MS;
  let lastStatus = 0;
  let attempt = 0;
  while (Date.now() < deadline) {
    attempt++;
    try {
      const r = await fetch(target, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
      });
      lastStatus = r.status;
      // 502/503/504 = Render dyno waking or briefly unavailable — retry until warm.
      if (r.status === 502 || r.status === 503 || r.status === 504) {
        if (Date.now() + 3000 < deadline) { await sleep(2500); continue; }
        res.status(503).json({ error: 'engine waking (cold start)', lastStatus, attempts: attempt });
        return;
      }
      const body = await r.text();
      res.setHeader('Content-Type', r.headers.get('content-type') || 'application/json');
      res.status(r.status).send(body);
      return;
    } catch (e) {
      lastStatus = 0;
      if (Date.now() + 3000 < deadline) { await sleep(2000); continue; }
      res.status(504).json({ error: 'engine unreachable', detail: String((e && e.message) || e), attempts: attempt });
      return;
    }
  }
  res.status(503).json({ error: 'engine unavailable after retries', lastStatus, attempts: attempt });
}
