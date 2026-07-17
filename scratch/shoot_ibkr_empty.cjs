// Empty-state + real-API smoke for the IBKR Terminal page.
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });

  // 1. Empty state: stub API with zero positions/trades
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  await page.route('**/api/ibkr*', (route) => {
    const u = new URL(route.request().url());
    const view = u.searchParams.get('view') || 'account';
    const body = view === 'account'
      ? { id: 1, net_liquidation: 100000, cash: 100000, buying_power: 400000,
          daily_pnl: null, unrealized_pnl: null, realized_pnl: null, currency: 'USD',
          updated_at: new Date().toISOString() }
      : [];
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('**/api/candles*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.goto('http://localhost:3001/ibkr-trades.html', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(1200);
  await page.click('#btnCrypto');
  await page.waitForTimeout(300);
  const emptyTxt = await page.textContent('#ibkrPositionsWrap');
  console.log('crypto empty state:', emptyTxt.trim().slice(0, 60));
  await page.screenshot({ path: 'scratch/site_ibkr_empty.png', fullPage: false });
  console.log('OK empty');
  await page.close();

  // 2. Real API (Supabase tables likely missing) — page must degrade, not blank
  const page2 = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  await page2.route('**/api/candles*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page2.goto('http://localhost:3001/ibkr-trades.html', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page2.waitForTimeout(8000);
  const errTxt = await page2.textContent('#ibkrPositionsWrap');
  console.log('real-API state:', errTxt.trim().slice(0, 80));
  await page2.screenshot({ path: 'scratch/site_ibkr_noapi.png', fullPage: false });
  console.log('OK noapi');

  await browser.close();
})().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
