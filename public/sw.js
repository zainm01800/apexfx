// APEX FX service worker.
// Strategy: NETWORK-FIRST for same-origin GETs (so deployed code/HTML is always
// fresh online — no stale-bundle bug), with a cache fallback only when offline.
// /api/* and all cross-origin requests are NEVER intercepted, so the live AI calls,
// candle/quant/crypto data and Supabase are completely unaffected.
const CACHE = 'apex-v3';   // bump → activate purges the old cache (was apex-v2)
const SHELL = [
  '/dashboard.html', '/history.html', '/backtest.html',
  '/dashboard.css', '/history.css', '/backtest.css',
  '/dashboard.js', '/history.js', '/backtest.js', '/backtest.worker.js',
  '/lib/ta.js', '/lib/regime.js', '/lib/confluence.js', '/lib/strategies.js',
  '/lib/metrics.js', '/lib/hypotheses.js', '/lib/runjob.js', '/lib/datasource.js',
  '/manifest.webmanifest', '/icon.svg',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // Fetch each shell asset FRESH (cache: 'reload') — older clients have these
      // assets pinned in the HTTP cache as immutable, so a plain addAll would precache
      // the stale copy. Reload bypasses that so the offline shell is current.
      .then((c) => Promise.all(SHELL.map((u) =>
        fetch(u, { cache: 'reload' }).then((r) => (r && r.ok) ? c.put(u, r) : null).catch(() => {})
      )))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // cross-origin (fonts, data) → browser handles
  if (url.pathname.startsWith('/api/')) return;       // live data → never intercept

  e.respondWith(
    // 'no-cache' = always revalidate against the server (cheap 304 via ETag when
    // unchanged). This is what lets deploys reach clients that previously pinned these
    // assets as immutable — without it, network-first still returns the stale HTTP-cache copy.
    fetch(req, { cache: 'no-cache' })
      .then((res) => {
        if (res && res.ok && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((cached) =>
          cached || (req.mode === 'navigate' ? caches.match('/dashboard.html') : Response.error())
        )
      )
  );
});
