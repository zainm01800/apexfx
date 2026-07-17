// UI verification for the 2026-07-17 fixes (uses system Chrome — repo chromium build
// is a stub mid-download). Serves public/ statically; /api/* will 404 (expected —
// pages catch it), so render functions are driven directly with synthetic data.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8917;
const BASE = `http://127.0.0.1:${PORT}`;

let pass = 0, fail = 0;
const ok = (name, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + name); cond ? pass++ : fail++; };

(async () => {
  const server = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: path.join(ROOT, 'public'), stdio: 'ignore' });
  await new Promise(r => setTimeout(r, 1200));

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    // ── history.html ──────────────────────────────────────────────────────────
    const page = await browser.newPage();
    const consoleErrors = [];
    page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + e.message));
    await page.goto(`${BASE}/history.html`, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(2500);   // let init() run its course (api fetches will fail — caught)

    // 1. XSS: a hostile DB lesson must render as LITERAL TEXT, never as an element.
    const xss = await page.evaluate(() => {
      window.__alerted = 0;
      window.alert = () => { window.__alerted++; };
      const payload = '<img src=x onerror=alert(1)>';
      const host = document.createElement('div');
      // Exactly the renderCard / openPreview sink shape:
      host.innerHTML = `<div>${lessonToHtml(payload)}</div>`;
      document.body.appendChild(host);
      return {
        imgs: host.querySelectorAll('img').length,
        text: host.textContent,
        html: host.innerHTML,
        alerted: window.__alerted,
      };
    });
    ok('XSS: no <img> element created from hostile lesson', xss.imgs === 0);
    ok('XSS: payload displays literally', xss.text.includes('<img src=x onerror=alert(1)>'));
    ok('XSS: escaped entities in HTML', xss.html.includes('&lt;img src=x onerror=alert(1)&gt;'));
    ok('XSS: alert never fired', xss.alerted === 0);

    // 1b. Legacy lesson shapes: <br> paragraph split still works; TICKET_ID comment stripped.
    const legacy = await page.evaluate(() => {
      const host = document.createElement('div');
      host.innerHTML = `<div>${lessonToHtml('First point.<br>Second point. <!-- TICKET_ID: 123 -->')}</div>`;
      document.body.appendChild(host);
      return { text: host.textContent, divs: host.querySelectorAll('div').length, html: host.innerHTML };
    });
    ok('Legacy: <br> still splits paragraphs', legacy.divs >= 2 && legacy.text.includes('First point.') && legacy.text.includes('Second point.'));
    ok('Legacy: TICKET_ID comment stripped, not shown', !legacy.text.includes('TICKET_ID') && !legacy.html.includes('<!--'));

    // 1c. Full renderCard with hostile lesson → inject into #scanGrid.
    const card = await page.evaluate(() => {
      window.__alerted = 0;
      const row = {
        id: 'TEST_1', symbol: 'TEST/USD', asset_type: 'Forex', verdict: 'BUY', confidence: 70,
        price: 100, entry_zone: '99 - 100', target_price: 110, stop_loss: 90, risk_reward: '1:2',
        outcome: 'ambiguous', outcome_date: '2026-07-16T00:00:00Z', created_at: '2026-07-15T00:00:00Z',
        lesson: '<img src=x onerror=alert(1)>', summary: '<script>alert(2)</script>', key_reasons: '[]',
      };
      const g = { symbol: row.symbol, current: row, scans: [row], trail: [], resolved: 1, wins: 0, losses: 1, winRate: 0, anchorFlag: null, lesson: row.lesson, ts: 1 };
      const grid = document.getElementById('scanGrid');
      grid.innerHTML = renderCard(g);
      return {
        imgs: grid.querySelectorAll('img').length,
        scripts: grid.querySelectorAll('script').length,
        lessonText: (grid.querySelector('.sc-lesson') || {}).textContent || '',
        summaryText: (grid.querySelector('.sc-summary') || {}).textContent || '',
        outcomeText: (grid.querySelector('.sc-outcome') || {}).textContent || '',
        alerted: window.__alerted,
      };
    });
    ok('renderCard: no <img>/<script> elements from hostile lesson+summary', card.imgs === 0 && card.scripts === 0);
    ok('renderCard: lesson payload shown literally', card.lessonText.includes('<img src=x onerror=alert(1)>'));
    ok('renderCard: summary payload shown literally', card.summaryText.includes('<script>alert(2)</script>'));
    ok('renderCard: ambiguous outcome labelled', card.outcomeText.includes('Ambiguous'));
    ok('renderCard: alert never fired', card.alerted === 0);

    // 2. Learning-by-setup panel with synthetic resolved rows.
    const learn = await page.evaluate(() => {
      const mk = (i, outcome, asset, style, regime, lesson) => ({
        id: `T_${i}`, symbol: 'EUR/USD', asset_type: asset, verdict: 'BUY', confidence: 70,
        outcome, risk_reward: '1:2', lesson: lesson || '',
        outcome_date: `2026-07-${String(1 + (i % 9)).padStart(2, '0')}T10:00:00Z`,
        created_at: '2026-06-01T00:00:00Z',
        setup_features: { style, regime },
      });
      _allRows = [
        mk(1, 'tp_hit', 'Forex', 'swing', 'trend', 'Momentum continuation worked.'),
        mk(2, 'tp_hit', 'Forex', 'swing', 'trend'),
        mk(3, 'sl_hit', 'Forex', 'swing', 'trend'),
        mk(4, 'ambiguous', 'Forex', 'swing', 'trend'),
        mk(5, 'sl_hit', 'Stock', 'intraday', 'range', 'Chop killed the breakout.'),
        mk(6, 'pending', 'Stock', 'intraday', 'range'),
      ];
      renderLearningPanel();
      const el = document.getElementById('learnBoard');
      const rows = [...el.querySelectorAll('.learn-row')].map(r => r.textContent);
      return { html: el.innerHTML, rows };
    });
    const fxRow = learn.rows.find(t => t.includes('Forex'));
    const stRow = learn.rows.find(t => t.includes('Stock'));
    ok('learn: Forex group n=4 (incl ambiguous), 67% win rate, lesson shown',
      !!fxRow && fxRow.includes('n=4') && fxRow.includes('+1') && fxRow.includes('67%') && fxRow.includes('Momentum continuation worked.'));
    ok('learn: avgR +1.00R for 2W(+2R each)/1L(-1R)', !!fxRow && fxRow.includes('+1.00R'));
    ok('learn: Stock group 0% win, pending row excluded', !!stRow && stRow.includes('n=1') && stRow.includes('0%'));
    ok('learn: no group for pending-only rows', learn.rows.length === 2);

    // 3. Paper card: synthetic rows then empty state.
    const paper = await page.evaluate(() => {
      renderPaperCard([
        { date: '2026-07-15', equity: 10000, cash: 10000, n_open: 0, gross_exposure_x: 0, day_pnl: 0, cum_pnl: 0, drawdown_from_peak: 0 },
        { date: '2026-07-16', equity: 10120, cash: 10050, n_open: 2, gross_exposure_x: 1.4, day_pnl: 120, cum_pnl: 120, drawdown_from_peak: 0 },
        { date: '2026-07-17', equity: 10040, cash: 10050, n_open: 3, gross_exposure_x: 2.1, day_pnl: -80, cum_pnl: 40, drawdown_from_peak: 0.0066 },
      ]);
      const el = document.getElementById('paperCard');
      const withData = {
        text: el.textContent,
        svg: !!el.querySelector('svg.pp-spark polyline'),
        stats: el.querySelectorAll('.acc-stat').length,
      };
      renderPaperCard([]);
      const empty = { text: el.textContent, svg: !!el.querySelector('svg') };
      return { withData, empty };
    });
    ok('paper: book label + stats + sparkline rendered',
      paper.withData.text.includes('book_d_multiasset_252') && paper.withData.svg && paper.withData.stats === 5);
    ok('paper: equity/day/cum/DD shown (£10,040 · -£80 · +£40 · 0.7% of 15% HALT)',
      paper.withData.text.includes('£10,040') && paper.withData.text.includes('-£80') && paper.withData.text.includes('+£40') && paper.withData.text.includes('0.7%'));
    ok('paper: empty state when no rows', paper.empty.text.includes('No paper-trading rows yet') && !paper.empty.svg);

    const histErrors = consoleErrors.filter(e => !/fetch|net::|Failed to load resource|\/api\//i.test(e));
    ok('history.html: no NEW console errors (only expected /api 404s)', histErrors.length === 0);
    if (histErrors.length) console.log('    console:', histErrors.slice(0, 5));

    // ── mt4-trades.html ───────────────────────────────────────────────────────
    const page2 = await browser.newPage();
    const consoleErrors2 = [];
    page2.on('console', m => { if (m.type() === 'error') consoleErrors2.push(m.text()); });
    page2.on('pageerror', e => consoleErrors2.push('PAGEERROR: ' + e.message));
    await page2.goto(`${BASE}/mt4-trades.html`, { waitUntil: 'load', timeout: 30000 });
    await page2.waitForTimeout(2500);

    const xss2 = await page2.evaluate(() => {
      window.__alerted = 0;
      window.alert = () => { window.__alerted++; };
      const payload = '<img src=x onerror=alert(1)>';
      // Exactly the renderLessonCardForTrade AI-lesson sink:
      const brSplit = '</div><div style="margin-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.04); padding-top: 6px;">';
      const host = document.createElement('div');
      host.innerHTML = `<div>${escHtml(formatLessonText(payload)).replace(/&lt;br\s*\/?&gt;/gi, brSplit)}</div>`;
      document.body.appendChild(host);
      // …and the trusted-HTML fallback path must still render formatting.
      const host2 = document.createElement('div');
      host2.innerHTML = `<div>${formatLessonText('<strong>✅ ok</strong><br>line2').replace(/<br>/gi, brSplit)}</div>`;
      document.body.appendChild(host2);
      return {
        imgs: host.querySelectorAll('img').length,
        literal: host.textContent.includes('<img src=x onerror=alert(1)>'),
        fallbackStrong: host2.querySelectorAll('strong').length,
        alerted: window.__alerted,
      };
    });
    ok('mt4 XSS: AI lesson payload inert + literal', xss2.imgs === 0 && xss2.literal && xss2.alerted === 0);
    ok('mt4: hardcoded fallback HTML still formatted', xss2.fallbackStrong === 1);

    const mt4Errors = consoleErrors2.filter(e => !/fetch|net::|Failed to load resource|\/api\/|Error fetching MT4/i.test(e));
    ok('mt4-trades.html: no NEW console errors (only expected /api 404s)', mt4Errors.length === 0);
    if (mt4Errors.length) console.log('    console:', mt4Errors.slice(0, 5));

    await page2.close();
    await page.close();
  } finally {
    await browser.close();
    server.kill();
  }

  console.log(`\nUI verification: ${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
