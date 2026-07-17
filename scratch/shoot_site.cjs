const { chromium } = require('playwright');
(async () => {
  const pages = [
    ['dashboard', 'https://apexfx.vercel.app/dashboard.html?sym=EUR/USD&auto=0'],
    ['history', 'https://apexfx.vercel.app/history.html'],
    ['mt4-trades', 'https://apexfx.vercel.app/mt4-trades.html'],
    ['track-record', 'https://apexfx.vercel.app/track-record.html'],
    ['backtest', 'https://apexfx.vercel.app/backtest.html'],
  ];
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 2400 } });
  for (const [name, url] of pages) {
    try {
      await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
      await page.waitForTimeout(4000);
      await page.screenshot({ path: `scratch/site_${name}.png`, fullPage: false });
      console.log(`OK ${name}`);
    } catch (e) { console.log(`FAIL ${name}: ${e.message.slice(0, 120)}`); }
  }
  await browser.close();
})();
