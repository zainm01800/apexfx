// Visual snapshot: paper card + learning panel + a scan card with synthetic data.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8918;
const BASE = `http://127.0.0.1:${PORT}`;

(async () => {
  const server = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: path.join(ROOT, 'public'), stdio: 'ignore' });
  await new Promise(r => setTimeout(r, 1200));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1400 } });
    await page.goto(`${BASE}/history.html`, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(2000);
    await page.evaluate(() => {
      document.getElementById('histLoading').style.display = 'none';
      // Paper card with ~3 weeks of equity.
      const rows = [];
      let eq = 10000;
      for (let i = 0; i < 21; i++) {
        eq += [30, -45, 80, 120, -60, 40, 15][i % 7];
        rows.push({ date: `2026-07-${String(i + 1).padStart(2, '0')}`, equity: eq, cash: 10000, n_open: i % 4, gross_exposure_x: 1.8, day_pnl: i ? eq - rows[i - 1].equity : 0, cum_pnl: eq - 10000, drawdown_from_peak: Math.max(0, (10200 - eq) / 10200) });
      }
      renderPaperCard(rows);
      // Learning panel.
      const mk = (i, outcome, asset, style, regime, lesson) => ({
        id: `T_${i}`, symbol: 'EUR/USD', asset_type: asset, verdict: 'BUY', confidence: 70,
        outcome, risk_reward: '1:2', lesson: lesson || '',
        outcome_date: `2026-07-${String(1 + (i % 9)).padStart(2, '0')}T10:00:00Z`, created_at: '2026-06-01T00:00:00Z',
        setup_features: { style, regime },
      });
      _allRows = [
        mk(1, 'tp_hit', 'Forex', 'swing', 'trend', 'Momentum continuation after the 50-SMA retest worked — keep requiring the retest, not the first touch.'),
        mk(2, 'tp_hit', 'Forex', 'swing', 'trend'), mk(3, 'sl_hit', 'Forex', 'swing', 'trend'),
        mk(4, 'ambiguous', 'Forex', 'swing', 'trend'),
        mk(5, 'sl_hit', 'Stock', 'intraday', 'range', 'Chop killed the breakout; range regime needs confirmation from volume expansion first.'),
        mk(6, 'tp_hit', 'Stock', 'intraday', 'range'), mk(7, 'sl_hit', 'Stock', 'intraday', 'range'),
        mk(8, 'tp_hit', 'Crypto', 'position', 'volatile', 'Wider stop survived the wick — position style pays in volatile regimes.'),
      ];
      renderLearningPanel();
      // One scan card incl. a hostile lesson + ambiguous outcome.
      const row = {
        id: 'TEST_1', symbol: 'EUR/USD', asset_type: 'Forex', verdict: 'BUY', confidence: 72,
        price: 1.085, entry_zone: '1.083 - 1.086', target_price: 1.11, stop_loss: 1.06, risk_reward: '1:2.2',
        outcome: 'ambiguous', outcome_date: '2026-07-16T00:00:00Z', created_at: '2026-07-15T10:00:00Z',
        lesson: '<img src=x onerror=alert(1)> — a stored payload must show literally, like this.',
        summary: 'Bullish continuation above the range high.',
      };
      document.getElementById('scanGrid').innerHTML = renderCard({ symbol: row.symbol, current: row, scans: [row], trail: [], resolved: 4, wins: 2, losses: 2, winRate: 50, anchorFlag: null, lesson: row.lesson, ts: 1 });
    });
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(ROOT, 'scratch', 'verify_panels.png'), fullPage: false });
    console.log('SHOT-OK');
  } finally {
    await browser.close();
    server.kill();
  }
})().catch(e => { console.error('FATAL', e); process.exit(1); });
