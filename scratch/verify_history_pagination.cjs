// Smoke test for history.html fetch-level pagination (egress diet 2026-07-17).
// Serves public/ statically and STUBS /api/* with a 250-row synthetic pool, then
// verifies: initial load fetches limit=100 (not 1000), the page renders + stats
// compute over the loaded set, and "Load older" grows the window 100→200→300
// (bounded), hiding itself once the pool is exhausted.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8923;
const BASE = `http://127.0.0.1:${PORT}`;

let pass = 0, fail = 0;
const ok = (name, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + name); cond ? pass++ : fail++; };

// 250 rows, newest first, all resolved WITH lessons (so no candle/AI fan-out).
const SYMS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'XAU/USD'];
const POOL = Array.from({ length: 250 }, (_, i) => {
  const ts = Date.UTC(2026, 6, 17, 12) - i * 3600 * 1000;
  const sym = SYMS[i % SYMS.length];
  return {
    id: `${sym.replace('/', '')}_${ts}`,
    symbol: sym,
    asset_type: 'Forex',
    analysis_date: new Date(ts).toISOString().slice(0, 10),
    price: 100, entry_zone: '99 - 100', target_price: 110, stop_loss: 90,
    risk_reward: '1:2', verdict: 'BUY', confidence: 70,
    outcome: i % 2 ? 'tp_hit' : 'sl_hit',
    outcome_price: i % 2 ? 110 : 90,
    outcome_date: new Date(ts + 86400000).toISOString(),
    created_at: new Date(ts).toISOString(),
    lesson: 'Post-mortem lesson.',
    summary: 's', key_reasons: '[]',
    setup_features: { style: 'swing', regime: 'trend' },
  };
});

(async () => {
  const server = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: path.join(ROOT, 'public'), stdio: 'ignore' });
  await new Promise(r => setTimeout(r, 1200));

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const consoleErrors = [];
    page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + e.message));

    const memoryLimits = [];
    // Playwright matches routes in reverse registration order — catch-all first.
    await page.route('**/api/**', route => route.fulfill({ json: [] }));
    await page.route('**/api/memory*', route => {
      const url = new URL(route.request().url());
      if (route.request().method() !== 'GET') return route.fulfill({ json: { ok: true } });
      const limit = parseInt(url.searchParams.get('limit') || '80', 10);
      memoryLimits.push(limit);
      return route.fulfill({ json: POOL.slice(0, limit) });
    });

    await page.goto(`${BASE}/history.html`, { waitUntil: 'load', timeout: 30000 });
    await page.waitForFunction(() =>
      (document.querySelector('#hsStat0 .hs-label') || {}).textContent?.includes('scans'), null, { timeout: 15000 });

    // 1. Initial fetch used the small window, not the old flat 1000.
    ok('initial /api/memory fetch used limit=100', memoryLimits[0] === 100);
    ok('no limit=1000 fetch on load', !memoryLimits.includes(1000));

    // 2. Page renders: loading hidden, cards present, stats over the loaded 100.
    const state1 = await page.evaluate(() => ({
      loadingHidden: document.getElementById('histLoading').style.display === 'none',
      cards: document.querySelectorAll('#scanGrid > *').length,
      statLabel: document.querySelector('#hsStat0 .hs-label').textContent,
      tp: document.querySelector('#hsStat1 .hs-val').textContent,
      scoreboard: document.getElementById('accBoard').textContent.length > 0,
      learn: document.getElementById('learnBoard').textContent.length > 0,
      wrapShown: document.getElementById('loadOlderWrap').style.display !== 'none',
      btnShown: document.getElementById('loadOlderBtn').style.display !== 'none',
      btnText: document.getElementById('loadOlderBtn').textContent,
      note: document.getElementById('loadOlderNote').textContent,
    }));
    ok('loading hidden, cards rendered', state1.loadingHidden && state1.cards > 0);
    ok('stats compute over loaded 100 scans', state1.statLabel.includes('100 scans'));
    ok('scoreboard + learning panel rendered', state1.scoreboard && state1.learn);
    ok('Load older visible with note', state1.wrapShown && state1.btnShown &&
      state1.btnText.includes('Load older') && state1.note.includes('older history loads on demand'));

    // 3. Click → window grows to 200, stats recompute.
    await page.click('#loadOlderBtn');
    await page.waitForFunction(() =>
      (document.querySelector('#hsStat0 .hs-label') || {}).textContent?.includes('200 scans'), null, { timeout: 15000 });
    ok('click 1 fetched limit=200', memoryLimits.includes(200));
    const btn2 = await page.evaluate(() => document.getElementById('loadOlderBtn').style.display !== 'none');
    ok('button still offered (200 < 300 cap)', btn2);

    // 4. Click → window grows to 300 (bounded); pool only has 250 → button retires.
    await page.click('#loadOlderBtn');
    await page.waitForFunction(() =>
      (document.querySelector('#hsStat0 .hs-label') || {}).textContent?.includes('250 scans'), null, { timeout: 15000 });
    ok('click 2 fetched limit=300 (bounded)', memoryLimits.includes(300) && !memoryLimits.some(l => l > 300));
    const state3 = await page.evaluate(() => ({
      btnShown: document.getElementById('loadOlderBtn').style.display !== 'none',
      note: document.getElementById('loadOlderNote').textContent,
      tp: document.querySelector('#hsStat1 .hs-val').textContent,
    }));
    ok('button hidden once window exhausted', !state3.btnShown);
    ok('note reflects loaded set (250 newest)', state3.note.includes('250 newest'));
    ok('TP stat counts full loaded set (125)', state3.tp === '125');

    const errs = consoleErrors.filter(e => !/fetch|net::|Failed to load resource|\/api\//i.test(e));
    ok('no unexpected console errors', errs.length === 0);
    if (errs.length) console.log('    console:', errs.slice(0, 5));

    await page.close();
  } finally {
    await browser.close();
    server.kill();
  }

  console.log(`\nPagination smoke: ${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
