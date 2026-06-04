// APEX FX service worker.
// Strategy: NETWORK-FIRST for same-origin GETs (so deployed code/HTML is always
// fresh online — no stale-bundle bug), with a cache fallback only when offline.
// /api/* and all cross-origin requests are NEVER intercepted, so the live AI calls,
// candle/quant/crypto data and Supabase are completely unaffected.
const CACHE = 'apex-v2';
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
      .then((c) => c.addAll(SHELL).catch(() => {}))   // best-effort precache of the app shell
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
    fetch(req)
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
