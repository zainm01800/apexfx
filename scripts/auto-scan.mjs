#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════════════════════
// ApexFX Auto-Scan — keeps the learning loops fed
//
// Drives the REAL deployed dashboard headlessly and runs a Deep Analyse on a fixed
// watchlist, once per run. Because it uses the live site, it reuses 100% of the
// committee pipeline — verdict, setup_features vector, saveToMemory — with zero
// logic duplicated here. Each scan also resolves that symbol's prior open outcomes
// (the dashboard does this on scan), and a final History-page load resolves the
// rest. Over time this accumulates the resolved, feature-tagged rows that the
// confidence-calibration and structural meta-label loops need before they activate.
//
// USAGE:  node scripts/auto-scan.mjs
// CONFIG: set APEX_SCAN_SYMBOLS="NVDA,EUR/USD,BTC/USD" to override the watchlist,
//         APEX_SCAN_BASE to point at a different deployment.
// ══════════════════════════════════════════════════════════════════════════════

import { chromium } from 'playwright';

const BASE = (process.env.APEX_SCAN_BASE || 'https://apexfx.vercel.app').replace(/\/$/, '');

// Diverse watchlist on purpose: stocks + ETFs + crypto + FX, spread across SECTORS
// (tech / financials / energy), asset classes, and likely regimes, so the structural
// meta-label + lessons library see real variety (several also carry COT positioning).
// Curated & liquid on purpose — random/illiquid tickers have flaky candle data and
// never resolve, which is noise, not signal. Edit freely — these are just the seeds.
const SYMBOLS = (process.env.APEX_SCAN_SYMBOLS ||
  'NVDA,AAPL,MSFT,AMZN,TSLA,JPM,XOM,SPY,QQQ,GLD,TLT,BTC/USD,ETH/USD,SOL/USD,EUR/USD,USD/JPY')
  .split(',').map(s => s.trim()).filter(Boolean);

const PER_SYMBOL_TIMEOUT_MS = 210000;   // committee can take ~60–90s; allow for one internal retry

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function scanOne(page, sym) {
  // auto=1 tags the saved row's setup_features so the History scoreboard can tell
  // bot-generated scans apart from the user's own calls (keeps personal stats honest).
  await page.goto(`${BASE}/dashboard.html?sym=${encodeURIComponent(sym)}&auto=1`, { waitUntil: 'load', timeout: 60000 });
  await page.waitForSelector('#analyseBtn', { timeout: 30000 });
  await sleep(1200);                      // let init() prefill the symbol + wire handlers
  await page.click('#analyseBtn');

  // Wait until the results, error, or cooldown section becomes visible.
  await page.waitForFunction(() => {
    const vis = id => { const el = document.getElementById(id); return el && getComputedStyle(el).display !== 'none'; };
    return vis('resultsSection') || vis('errorSection') || vis('cooldownSection');
  }, undefined, { timeout: PER_SYMBOL_TIMEOUT_MS });   // 3rd arg = options (2nd is the page-fn arg)

  return page.evaluate(() => {
    const vis = id => { const el = document.getElementById(id); return el && getComputedStyle(el).display !== 'none'; };
    if (vis('resultsSection')) {
      const verdict = document.getElementById('verdictBadge')?.textContent?.trim() || '';
      const conf    = document.getElementById('confPct')?.textContent?.trim() || '';
      return { status: 'ok', verdict, conf };
    }
    if (vis('cooldownSection')) return { status: 'cooldown' };
    const msg = document.getElementById('errorMsg')?.textContent?.trim() || 'unknown error';
    return { status: 'error', msg };
  });
}

// Re-validate an EXISTING open trade (validity re-check) without creating a new trade:
// drives the ?validate=ID flow, which appends a validation record (still valid /
// weakening / invalidated / now-actionable) so open "wait" setups are auto-reviewed.
async function validateOne(page, sym, id) {
  await page.goto(`${BASE}/dashboard.html?sym=${encodeURIComponent(sym)}&validate=${encodeURIComponent(id)}&auto=1`, { waitUntil: 'load', timeout: 60000 });
  await page.waitForSelector('#analyseBtn', { timeout: 30000 });
  // Wait until the dashboard has loaded the validate target, else a click would run a
  // normal scan and create a new trade instead of re-checking the existing one.
  await page.waitForFunction(() => window.__apexValidateReady === true, undefined, { timeout: 15000 }).catch(() => {});
  await sleep(400);
  await page.click('#analyseBtn');
  await page.waitForFunction(() => {
    const vis = id => { const el = document.getElementById(id); return el && getComputedStyle(el).display !== 'none'; };
    return vis('resultsSection') || vis('errorSection') || vis('cooldownSection');
  }, undefined, { timeout: PER_SYMBOL_TIMEOUT_MS });
  return page.evaluate(() => {
    const vis = id => { const el = document.getElementById(id); return el && getComputedStyle(el).display !== 'none'; };
    if (vis('resultsSection')) {
      const b = document.getElementById('compareBanner');
      const m = b ? (b.innerText.match(/(STILL VALID|WEAKENING|INVALIDATED|NOW ACTIONABLE|STILL WAITING)/i) || [''])[0] : '';
      return { status: 'ok', note: m || 're-checked' };
    }
    if (vis('cooldownSection')) return { status: 'cooldown' };
    return { status: 'error', msg: document.getElementById('errorMsg')?.textContent?.trim() || 'unknown' };
  });
}

async function main() {
  console.log(`[auto-scan] ${SYMBOLS.length} symbols against ${BASE}`);
  console.log(`[auto-scan] watchlist: ${SYMBOLS.join(', ')}`);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  const summary = { ok: 0, error: 0, cooldown: 0 };
  for (const sym of SYMBOLS) {
    const t0 = Date.now();
    try {
      const r = await scanOne(page, sym);
      const secs = ((Date.now() - t0) / 1000).toFixed(0);
      if (r.status === 'ok') {
        summary.ok++;
        console.log(`  ✓ ${sym.padEnd(9)} ${r.verdict} ${r.conf}  (${secs}s)`);
      } else if (r.status === 'cooldown') {
        summary.cooldown++;
        console.log(`  ⏳ ${sym.padEnd(9)} skipped (cooldown)  (${secs}s)`);
      } else {
        summary.error++;
        console.log(`  ✗ ${sym.padEnd(9)} ${r.msg}  (${secs}s)`);
      }
    } catch (e) {
      summary.error++;
      console.log(`  ✗ ${sym.padEnd(9)} ${e.message?.split('\n')[0] || e}  (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
    }
    await sleep(1500);   // small spacer between symbols to ease rate limits
  }

  // ── Auto re-validation of OPEN trades ───────────────────────────────────────
  // Re-check every open trade (the "wait" setups you're watching) to see if the call
  // still holds or should change. Reuses the validity-re-check flow → appends a
  // validation record per trade. Capped to keep AI usage bounded.
  const MAX_REVAL = parseInt(process.env.APEX_REVALIDATE_MAX || '10', 10);
  try {
    // Clear cooldowns set during the scan loop so we can re-check freely.
    await page.evaluate(() => { try { localStorage.clear(); } catch {} });
    const open = await page.evaluate(async () => {
      const rows = await fetch('/api/memory?all=true&limit=200').then(r => r.json()).catch(() => []);
      return (Array.isArray(rows) ? rows : [])
        .filter(r => (r.outcome == null || r.outcome === 'pending') && r.target_price && r.stop_loss
          && /BUY|SELL|SHORT|LONG|WAIT|HOLD|NO_EDGE/i.test(r.verdict || ''))
        .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
        .slice(0, 50)
        .map(r => ({ id: r.id, sym: r.symbol }));
    });
    const toReval = open.slice(0, MAX_REVAL);
    console.log(`[auto-scan] re-validating ${toReval.length} open trade(s) (of ${open.length} open)`);
    let rok = 0, rerr = 0;
    for (const t of toReval) {
      const t0 = Date.now();
      try {
        const r = await validateOne(page, t.sym, t.id);
        const secs = ((Date.now() - t0) / 1000).toFixed(0);
        if (r.status === 'ok') { rok++; console.log(`  🔁 ${t.sym.padEnd(9)} ${r.note}  (${secs}s)`); }
        else if (r.status === 'cooldown') console.log(`  ⏳ ${t.sym.padEnd(9)} re-check skipped (cooldown)  (${secs}s)`);
        else { rerr++; console.log(`  ✗ ${t.sym.padEnd(9)} ${r.msg}  (${secs}s)`); }
      } catch (e) {
        rerr++; console.log(`  ✗ ${t.sym.padEnd(9)} ${e.message?.split('\n')[0] || e}`);
      }
      await sleep(1500);
    }
    console.log(`[auto-scan] re-validation done — ${rok} re-checked, ${rerr} errored.`);
  } catch (e) {
    console.log(`[auto-scan] re-validation phase failed (non-fatal): ${e.message}`);
  }

  // Final pass: load History so resolveIfPending() updates outcomes for every
  // open setup (not just the symbols scanned this run).
  try {
    await page.goto(`${BASE}/history.html`, { waitUntil: 'load', timeout: 60000 });
    await sleep(20000);
    console.log('[auto-scan] History loaded — pending outcomes resolved where possible.');
  } catch (e) {
    console.log(`[auto-scan] History resolve pass failed (non-fatal): ${e.message}`);
  }

  await browser.close();
  console.log(`[auto-scan] done — ${summary.ok} scanned, ${summary.error} errored, ${summary.cooldown} cooled down.`);
}

main().catch(e => { console.error('[auto-scan] fatal:', e); process.exit(1); });
