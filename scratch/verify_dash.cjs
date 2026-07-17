const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const PORT = 8921;
let pass = 0, fail = 0;
const ok = (n, c) => { console.log((c ? '  ✓ ' : '  ✗ ') + n); c ? pass++ : fail++; };
(async () => {
  const server = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: path.join(ROOT, 'public'), stdio: 'ignore' });
  await new Promise(r => setTimeout(r, 1200));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errs = [];
    page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
    page.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
    await page.goto(`http://127.0.0.1:${PORT}/dashboard.html`, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(3000);
    const r = await page.evaluate(() => {
      const DAY = 86400, T0 = Date.UTC(2026, 6, 15) / 1000;
      const B = (o,h,l,c,d) => ({ time: T0 + d * DAY, open:o, high:h, low:l, close:c, volume: 1000 });
      const mk = () => ({ id: 'T_1', symbol: 'EUR/USD', asset_type: 'Forex', verdict: 'BUY', price: 100,
        entry_zone: '100', target_price: 110, stop_loss: 90, outcome: 'pending',
        analysis_date: '2026-07-15', created_at: '2026-07-15T00:00:00Z' });
      const both = mk(); resolveOutcomes([both], [B(100,111,89,100,1)], '1d');
      const tp   = mk(); resolveOutcomes([tp],   [B(100,111,95,105,1)], '1d');
      const sl   = mk(); resolveOutcomes([sl],   [B(100,105,89,95,1)],  '1d');
      return { both: both.outcome, tp: tp.outcome, sl: sl.outcome, bothPrice: both.outcome_price };
    });
    ok('dashboard resolveOutcomes: both-in-one-bar → ambiguous (price null)', r.both === 'ambiguous' && r.bothPrice == null);
    ok('dashboard resolveOutcomes: TP-only → tp_hit, SL-only → sl_hit', r.tp === 'tp_hit' && r.sl === 'sl_hit');
    const real = errs.filter(e => !/fetch|net::|Failed to load resource|\/api\/|401|403|404/i.test(e));
    ok('dashboard.html: no NEW console errors', real.length === 0);
    if (real.length) console.log('   ', real.slice(0, 5));
  } finally { await browser.close(); server.kill(); }
  console.log(`\ndash verify: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
