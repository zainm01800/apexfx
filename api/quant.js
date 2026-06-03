// /api/quant — same-origin proxy to the hosted quant engine on Render.
//
// Why a flat ?p= route instead of /api/quant/<path>: a browser calling the
// Render free-tier engine directly gets HTTP 503 while the dyno wakes, and
// Render's 503 page has no CORS header, so the fetch dies as "Failed to fetch"
// and the cross-check shows OFFLINE — even though the engine answers
// server-to-server fine (curl: 200 in <1s). This proxy makes the call
// same-origin and runs it server-side (like curl), where it succeeds, and
// retries through cold-start 502/503s. The engine sub-path (incl. its own query
// string) is passed URL-encoded in ?p=, so this stays a single FLAT route that
// resolves exactly like /api/quote — no dynamic/catch-all routing to fight with
// the /public catch-all rewrite. Built by public/{dashboard,quant}.js → engUrl().
//
//   /api/quant?p=%2Fhealth
//   /api/quant?p=%2Fregime%2FNVDA
//   /api/quant?p=%2Frisk%2FNVDA%3Fequity%3D100000

export const config = { maxDuration: 60 };

const ENGINE = 'https://apex-quant-engine.onrender.com';
const BUDGET_MS = 55000;           // stay safely under the 60s function ceiling
const ATTEMPT_TIMEOUT_MS = 18000;  // per-try cap; a warm engine answers in <1s
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }
  if (req.method !== 'GET') { res.status(405).json({ error: 'method not allowed' }); return; }

  let p = req.query && req.query.p;
  if (Array.isArray(p)) p = p[0];
  if (!p || typeof p !== 'string') { res.status(400).json({ error: 'missing ?p= engine path' }); return; }
  if (!p.startsWith('/')) p = '/' + p;
  // Forward only to the engine's own paths — never an arbitrary absolute URL.
  if (p.startsWith('//') || /^\/+https?:/i.test(p)) { res.status(400).json({ error: 'invalid path' }); return; }
  const target = ENGINE + p;

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
