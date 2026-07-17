// Screenshot smoke for the new IBKR Terminal page (public/ibkr-trades.html).
// Serves from the local dev server (:3001) with /api/ibkr STUBBED at the
// browser level (Supabase tables may not exist yet). Usage:
//   node scratch/shoot_ibkr.cjs
const { chromium } = require('playwright');

const NOW = new Date();
const iso = (d) => d.toISOString();
const minsAgo = (m) => iso(new Date(NOW.getTime() - m * 60000));

const ACCOUNT = {
  id: 1,
  net_liquidation: 100523.45,
  cash: 99800.12,
  buying_power: 400210.5,
  daily_pnl: 145.67,
  unrealized_pnl: 523.45,
  realized_pnl: -12.34,
  currency: 'USD',
  updated_at: iso(NOW),
};

const POSITIONS = [
  { instrument: 'EUR/USD', direction: 'long', units: 20000, avg_price: 1.0850,
    market_value: 21720.0, unrealized_pnl: 20.0, asset_class: 'forex', updated_at: iso(NOW) },
  { instrument: 'GBP/JPY', direction: 'short', units: 10000, avg_price: 195.20,
    market_value: 13312.4, unrealized_pnl: -35.5, asset_class: 'forex', updated_at: iso(NOW) },
  { instrument: 'AAPL', direction: 'long', units: 44.02, avg_price: 227.5,
    market_value: 10036.56, unrealized_pnl: 22.01, asset_class: 'stocks', updated_at: iso(NOW) },
  { instrument: 'TSLA', direction: 'short', units: 15, avg_price: 250.1,
    market_value: 3742.5, unrealized_pnl: 9.0, asset_class: 'stocks', updated_at: iso(NOW) },
  { instrument: 'BTC/USD', direction: 'long', units: 0.5, avg_price: 65000,
    market_value: 32000.0, unrealized_pnl: 500.0, asset_class: 'crypto', updated_at: iso(NOW) },
];

const TRADES = [
  // EUR/USD round trip: win (+20)
  { exec_id: 'e1', instrument: 'EUR/USD', asset_class: 'forex', side: 'BUY', qty: 20000,
    price: 1.0840, commission: 2.0, exec_time: minsAgo(3000), synced_at: minsAgo(2999) },
  { exec_id: 'e2', instrument: 'EUR/USD', asset_class: 'forex', side: 'SELL', qty: 20000,
    price: 1.0850, commission: 2.0, exec_time: minsAgo(2000), synced_at: minsAgo(1999) },
  // GBP/JPY round trip: loss
  { exec_id: 'e3', instrument: 'GBP/JPY', asset_class: 'forex', side: 'SELL', qty: 10000,
    price: 195.00, commission: 2.0, exec_time: minsAgo(1500), synced_at: minsAgo(1499) },
  { exec_id: 'e4', instrument: 'GBP/JPY', asset_class: 'forex', side: 'BUY', qty: 10000,
    price: 195.20, commission: 2.0, exec_time: minsAgo(1400), synced_at: minsAgo(1399) },
  // Stocks
  { exec_id: 'e5', instrument: 'AAPL', asset_class: 'stocks', side: 'BUY', qty: 44.02,
    price: 227.5, commission: 1.0, exec_time: minsAgo(1200), synced_at: minsAgo(1199) },
  { exec_id: 'e6', instrument: 'TSLA', asset_class: 'stocks', side: 'SELL', qty: 15,
    price: 250.1, commission: 1.0, exec_time: minsAgo(900), synced_at: minsAgo(899) },
  // Crypto
  { exec_id: 'e7', instrument: 'BTC/USD', asset_class: 'crypto', side: 'BUY', qty: 0.5,
    price: 65000, commission: 3.25, exec_time: minsAgo(600), synced_at: minsAgo(599) },
];

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1600 } });

  await page.route('**/api/ibkr*', (route) => {
    const u = new URL(route.request().url());
    const view = u.searchParams.get('view') || 'account';
    const body = view === 'account' ? ACCOUNT : view === 'positions' ? POSITIONS : TRADES;
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  // Pulse bar calls a real data API — stub it quiet so the shot is deterministic.
  await page.route('**/api/candles*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));

  await page.goto('http://localhost:3001/ibkr-trades.html', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(1500);

  const tabs = [
    ['forex', '#btnForex'],
    ['stocks', '#btnStocks'],
    ['crypto', '#btnCrypto'],
  ];
  for (const [name, sel] of tabs) {
    await page.click(sel);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `scratch/site_ibkr_${name}.png`, fullPage: false });
    console.log(`OK ${name}`);
  }

  // Basic DOM assertions
  const netLiq = await page.textContent('#statNetLiq');
  const openCount = await page.textContent('#statOpenCount');
  console.log('header NetLiq:', netLiq, '| open count:', openCount);
  await page.click('#btnCrypto');
  const winRate = await page.textContent('#clsWinRate');
  const closedCount = await page.textContent('#clsClosedCount');
  console.log('crypto tab win rate:', winRate, '|', closedCount);

  await browser.close();
})().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
