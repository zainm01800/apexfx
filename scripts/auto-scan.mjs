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
