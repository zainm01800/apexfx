const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const PORT = 8919;
(async () => {
  const server = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: path.join(ROOT, 'public'), stdio: 'ignore' });
  await new Promise(r => setTimeout(r, 1200));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 1200 } });
    await page.goto(`http://127.0.0.1:${PORT}/history.html`, { waitUntil: 'load' });
    await page.waitForTimeout(1800);
    await page.evaluate(() => {
      document.getElementById('histLoading').style.display = 'none';
      const row = {
        id: 'TEST_1', symbol: 'EUR/USD', asset_type: 'Forex', verdict: 'BUY', confidence: 72,
        price: 1.085, entry_zone: '1.083 - 1.086', target_price: 1.11, stop_loss: 1.06, risk_reward: '1:2.2',
        outcome: 'ambiguous', outcome_date: '2026-07-16T00:00:00Z', created_at: '2026-07-15T10:00:00Z',
        lesson: '<img src=x onerror=alert(1)> — a stored payload must show literally, like this.<br>Second paragraph of the lesson.',
        summary: 'Bullish continuation above the range high.',
      };
      document.getElementById('scanGrid').innerHTML = renderCard({ symbol: row.symbol, current: row, scans: [row], trail: [], resolved: 4, wins: 2, losses: 2, winRate: 50, anchorFlag: null, lesson: row.lesson, ts: 1 });
    });
    await page.waitForTimeout(300);
    const card = await page.$('.scan-card');
    await card.screenshot({ path: path.join(ROOT, 'scratch', 'verify_card.png') });
    console.log('SHOT-OK');
  } finally { await browser.close(); server.kill(); }
})().catch(e => { console.error('FATAL', e); process.exit(1); });
