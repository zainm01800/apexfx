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
// USAGE:  node scripts/auto-scan.mjs            (rotate randomly over the universe)
//         node scripts/auto-scan.mjs --plan     (print what it WOULD scan, no browser)
// CONFIG: APEX_SCAN_MODE=midday|evening         picks the default style mix (else by UTC hour)
//         APEX_SCAN_MIX="scalp:6,intraday:10"   explicit style→count mix (overrides mode)
//         APEX_SCAN_SYMBOLS="NVDA,EUR/USD"      fixed list (scanned in every mix style)
//         APEX_SCAN_STYLES+APEX_SCAN_COUNT      legacy: COUNT picks × each style
//         APEX_SCAN_BASE                        points at a different deployment
// ══════════════════════════════════════════════════════════════════════════════

import { chromium } from 'playwright';

const BASE = (process.env.APEX_SCAN_BASE || 'https://apexfx.vercel.app').replace(/\/$/, '');

// The instrument UNIVERSE — mirrors public/backtest.js (keep in sync). Rotating
// RANDOMLY across this whole pool (instead of a fixed 8) gives the calibration +
// meta-label loops a broad, balanced dataset over time. Liquid names only — flaky
// candle data never resolves, which is noise not signal.
const UNIVERSE = {
  Forex:  ['EUR/USD','GBP/USD','USD/JPY','USD/CHF','AUD/USD','USD/CAD','NZD/USD','GBP/JPY','EUR/GBP','EUR/JPY'],
  Crypto: ['BTC/USD','ETH/USD','SOL/USD','BNB/USD','XRP/USD','ADA/USD','AVAX/USD','DOGE/USD','MATIC/USD','LINK/USD','ARB/USD','SUI/USD'],
  Stock:  ['NVDA','AAPL','MSFT','META','AMZN','GOOGL','TSLA','AMD','PLTR','TSM','NFLX','UBER'],
  ETF:    ['SPY','QQQ','IWM','GLD','TLT','XLK','XLE','XLF','ARKK','SMH','SOXX','XBI'],
};
// 2026 NYSE holidays — treat like weekends (stocks + ETFs closed → crypto only).
const US_HOLIDAYS_2026 = new Set([
  '2026-01-01','2026-01-19','2026-02-16','2026-04-03','2026-05-25',
  '2026-06-19','2026-07-03','2026-09-07','2026-11-26','2026-12-25',
]);

const PER_SYMBOL_TIMEOUT_MS = 210000;   // committee can take ~60–90s; allow for one internal retry

// ── Style mix ──────────────────────────────────────────────────────────────────
// Every trade style is scanned, but with different weights per run window so the
// learning loops get balanced data at ~200 scans/week total:
//   midday  (weekdays, markets OPEN)  → scalp + intraday — these need a live tape,
//            and they resolve in hours/days, so they feed calibration fastest.
//   evening (daily, after US close)   → swing + position on fresh daily/weekly bars.
//   weekends/US holidays              → crypto only (24/7), all four styles.
// Weekly volume: 5×(16+16) + 2×20 = 200 attempted scans, allocated per the
// learning-loop research: intraday 60 (30%) + swing 60 (30%) are the learning
// workhorses (7–30d resolution, best label fidelity); scalp 40 (20%) is capped
// because 15-min-delayed data makes its labels least trustworthy; position 40
// (20%) because 120-day expiries teach the slowest.
// Override with APEX_SCAN_MIX="style:count,..." or legacy APEX_SCAN_STYLES+COUNT.
const DEFAULT_MIX = {
  midday:  { scalp: 6, intraday: 10 },                       // 16 — markets open
  evening: { swing: 10, position: 6 },                       // 16 — daily bars fresh
  offday:  { scalp: 5, intraday: 5, swing: 5, position: 5 }, // 20 — crypto only
};

function runMode(d = new Date()) {
  const m = (process.env.APEX_SCAN_MODE || '').trim().toLowerCase();
  if (m === 'midday' || m === 'evening') return m;
  return d.getUTCHours() < 19 ? 'midday' : 'evening';
}

function parseMix(str) {
  const mix = {};
  for (const part of String(str).split(',')) {
    const [style, n] = part.split(':').map(s => s.trim());
    if (style && parseInt(n, 10) > 0) mix[style.toLowerCase()] = parseInt(n, 10);
  }
  return Object.keys(mix).length ? mix : null;
}

// Resolve this run's style→count mix from env or the defaults for today/this window.
function resolveMix(dt) {
  if (process.env.APEX_SCAN_MIX) {
    const m = parseMix(process.env.APEX_SCAN_MIX);
    if (m) return { mix: m, source: 'APEX_SCAN_MIX' };
  }
  // Legacy: APEX_SCAN_STYLES + APEX_SCAN_COUNT → COUNT picks scanned in EACH style.
  if (process.env.APEX_SCAN_STYLES) {
    const styles = process.env.APEX_SCAN_STYLES.split(',').map(s => s.trim()).filter(Boolean);
    const n = Math.max(1, parseInt(process.env.APEX_SCAN_COUNT || '10', 10));
    const m = {};
    for (const s of styles) m[s] = n;
    return { mix: m, source: 'APEX_SCAN_STYLES (legacy)' };
  }
  if (dt.cryptoOnly) return { mix: { ...DEFAULT_MIX.offday }, source: 'default offday (crypto-only)' };
  const mode = runMode();
  return { mix: { ...DEFAULT_MIX[mode] }, source: `default ${mode}` };
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// What's tradeable today (UTC): weekends + US holidays → CRYPTO ONLY (stocks/forex are
// closed, so their bars are stale and never resolve cleanly); weekdays → everything.
function dayType(d = new Date()) {
  const forced = process.env.APEX_SCAN_FORCE_DAY;   // 'weekday'|'weekend'|'us-holiday' — testing/manual
  if (forced) return { type: forced, cryptoOnly: forced !== 'weekday' };
  const day = d.getUTCDay(), dateStr = d.toISOString().slice(0, 10);
  if (day === 0 || day === 6)        return { type: 'weekend',    cryptoOnly: true };
  if (US_HOLIDAYS_2026.has(dateStr)) return { type: 'US-holiday', cryptoOnly: true };
  return { type: 'weekday', cryptoOnly: false };
}
const eligiblePool = (dt) => dt.cryptoOnly ? [...UNIVERSE.Crypto] : Object.values(UNIVERSE).flat();

// How many times each (symbol × style) cell has already been scanned — so each
// style's draw is biased toward names UNDER-sampled in THAT style, filling the
// dataset in balanced across the whole style×instrument grid. null on failure.
async function getScanCounts() {
  try {
    const rows = await fetch(`${BASE}/api/memory?all=true&lean=true&limit=1000`).then(r => r.json());
    if (!Array.isArray(rows)) return null;
    const c = {};   // 'SYM|style' → count  (missing style = legacy swing scans)
    for (const r of rows) {
      if (!r.symbol) continue;
      let f = r.setup_features;
      if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
      const style = (f && f.style ? String(f.style) : 'swing').toLowerCase();
      const key = `${r.symbol}|${style}`;
      c[key] = (c[key] || 0) + 1;
    }
    return c;
  } catch { return null; }
}

// Weighted random sample WITHOUT replacement; weight = 1/(scans+1) so rarely-scanned
// cells are favoured. Uniform when weightOf is null (count query failed).
function weightedSample(pool, n, weightOf) {
  const items = pool.map(s => ({ s, w: weightOf ? 1 / (weightOf(s) + 1) : 1 }));
  const out = [];
  n = Math.min(n, items.length);
  for (let k = 0; k < n; k++) {
    const total = items.reduce((a, b) => a + b.w, 0);
    let r = Math.random() * total, idx = 0;
    for (; idx < items.length; idx++) { r -= items[idx].w; if (r <= 0) break; }
    idx = Math.min(idx, items.length - 1);
    out.push(items[idx].s);
    items.splice(idx, 1);
  }
  return out;
}

// Decide this run's jobs: explicit symbol override (scanned in every mix style), else
// per-style coverage-weighted random draws from today's eligible pool. Each style
// draws INDEPENDENTLY (a symbol can be picked for scalp and swing — different trades).
// Returns { jobs: [{sym, style}], plan }.
async function selectJobs() {
  const dt = dayType();
  const { mix, source: mixSource } = resolveMix(dt);

  if (process.env.APEX_SCAN_SYMBOLS) {
    const syms = process.env.APEX_SCAN_SYMBOLS.split(',').map(s => s.trim()).filter(Boolean);
    const styles = Object.keys(mix);
    const jobs = [];
    for (const sym of syms) for (const style of styles) jobs.push({ sym, style });
    return { jobs, plan: { source: 'APEX_SCAN_SYMBOLS override', dayType: dt.type, mix, mixSource, poolSize: syms.length, cells: null } };
  }

  const pool = eligiblePool(dt);
  const counts = await getScanCounts();

  // Pseudo-replication guard (learning-loop research): while an instrument×style
  // trade is still OPEN, don't open a second one — the re-validation phase already
  // re-checks it. Stacked same-cell trades share one market move, so they'd inflate
  // the record's nominal N without adding independent information.
  let openCells = new Set();
  try {
    const open = await fetch(`${BASE}/api/memory?all=true&open=true&lean=true&limit=1000`).then(r => r.json());
    for (const r of (Array.isArray(open) ? open : [])) {
      if (!r.symbol || !r.target_price || !r.stop_loss) continue;
      let f = r.setup_features;
      if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
      openCells.add(`${r.symbol}|${(f && f.style ? String(f.style) : 'swing').toLowerCase()}`);
    }
  } catch { openCells = new Set(); }

  const jobs = [], cells = [];
  let skippedOpen = 0;
  for (const [style, n] of Object.entries(mix)) {
    const stylePool = pool.filter(s => !openCells.has(`${s}|${style}`));
    skippedOpen += pool.length - stylePool.length;
    const picked = weightedSample(stylePool, n, counts ? (s) => counts[`${s}|${style}`] || 0 : null);
    for (const sym of picked) {
      // ~15% directive-blind control arm (APEX_SCAN_CONTROL_PCT to tune/disable).
      const control = Math.random() < (parseFloat(process.env.APEX_SCAN_CONTROL_PCT || '15') / 100);
      jobs.push({ sym, style, control });
      if (counts) cells.push(`${sym}/${style}:${counts[`${sym}|${style}`] || 0}`);
    }
  }
  return {
    jobs,
    plan: {
      source: counts ? 'per-style coverage-weighted random' : 'uniform random (count query failed)',
      dayType: dt.type, mix, mixSource, poolSize: pool.length,
      openCellsExcluded: skippedOpen,
      cells: cells.length ? cells : null,
    },
  };
}

async function scanOne(page, sym, style = '', control = false) {
  // auto=1 tags the saved row's setup_features so the History scoreboard can tell
  // bot-generated scans apart from the user's own calls (keeps personal stats honest).
  // &style lets one run cover multiple horizons (e.g. swing + the faster-resolving
  // intraday) so the forward track record accumulates quicker.
  // &control=1 = DIRECTIVE-BLIND: the committee gets no calibration/meta-label/lesson
  // feedback blocks. ~15% of scans run blind as a permanent control arm — an A/B of
  // whether the learning loops help, and a guard against feedback self-fulfillment.
  const styleQ = style ? `&style=${encodeURIComponent(style)}` : '';
  const ctrlQ  = control ? '&control=1' : '';
  await page.goto(`${BASE}/dashboard.html?sym=${encodeURIComponent(sym)}&auto=1${styleQ}${ctrlQ}`, { waitUntil: 'load', timeout: 60000 });
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
  const PLAN_ONLY = process.argv.includes('--plan') || process.env.APEX_SCAN_PLAN === '1';

  const { jobs: JOBS, plan } = await selectJobs();
  const mixStr = Object.entries(plan.mix).map(([s, n]) => `${s}×${n}`).join(' + ');
  console.log(`[auto-scan] day=${plan.dayType} · mode mix=${mixStr} (${plan.mixSource}) · pool=${plan.poolSize} · ${JOBS.length} scans via ${plan.source}${plan.openCellsExcluded ? ` · ${plan.openCellsExcluded} open cells excluded (re-validated instead)` : ''}`);
  console.log(`[auto-scan] jobs: ${JOBS.map(j => `${j.sym}(${j.style})`).join(', ')}`);
  if (plan.cells) console.log(`[auto-scan] chosen cell-counts (lower = under-sampled → favoured): ${plan.cells.join(', ')}`);
  if (PLAN_ONLY) { console.log('[auto-scan] PLAN ONLY — not launching the browser. ✔'); return; }
  console.log(`[auto-scan] target: ${BASE}`);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  const summary = { ok: 0, error: 0, cooldown: 0 };
  for (const { sym, style, control } of JOBS) {
    const t0 = Date.now();
    const tag = `${sym} ${style}${control ? ' [ctrl]' : ''}`;
    try {
      const r = await scanOne(page, sym, style, control);
      const secs = ((Date.now() - t0) / 1000).toFixed(0);
      if (r.status === 'ok') {
        summary.ok++;
        console.log(`  ✓ ${tag.padEnd(20)} ${r.verdict} ${r.conf}  (${secs}s)`);
      } else if (r.status === 'cooldown') {
        summary.cooldown++;
        console.log(`  ⏳ ${tag.padEnd(20)} skipped (cooldown)  (${secs}s)`);
      } else {
        summary.error++;
        console.log(`  ✗ ${tag.padEnd(20)} ${r.msg}  (${secs}s)`);
      }
    } catch (e) {
      summary.error++;
      console.log(`  ✗ ${tag.padEnd(20)} ${e.message?.split('\n')[0] || e}  (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
    }
    await sleep(1500);   // small spacer to ease rate limits
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
      // open=true → ALL unresolved trades regardless of age (at 200 scans/week an
      // open trade can be far older than any recent-rows window).
      const rows = await fetch('/api/memory?all=true&open=true&lean=true&limit=1000').then(r => r.json()).catch(() => []);
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
