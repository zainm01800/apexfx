#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════════════════════
// ApexFX proximity watch — auto re-analyse a trade the moment it nears its entry.
//
// When an OPEN trade's live price gets VERY close to its entry zone, re-run the full
// committee (the validity re-check) so the call is fresh at the moment it matters most
// — i.e. the AI re-checks the entry conditions / confluence right before the trade
// would trigger, instead of relying on a hours-old read.
//
// Two modes so the EXPENSIVE browser only spins up when there's something to do
// (keeps CI cheap on a private repo):
//   --scan     : pure fetch — find close trades, write {candidates,count} to GITHUB_OUTPUT
//   --recheck  : launch Playwright and re-validate the ids in APEX_PROX_IDS
//
// CONFIG (env): APEX_PROX_PCT (% from entry to trigger, default 0.4), APEX_PROX_MAX
// (max re-checks/run, default 5), APEX_PROX_DEBOUNCE_H (skip if re-checked within N h,
// default 3), APEX_PROX_BASE (deployment).
// ══════════════════════════════════════════════════════════════════════════════
import { appendFileSync } from 'node:fs';

const BASE = (process.env.APEX_PROX_BASE || 'https://apexfx.vercel.app').replace(/\/$/, '');
const THRESHOLD  = parseFloat(process.env.APEX_PROX_PCT || '0.4');        // % from entry to trigger
const DEBOUNCE_H = parseFloat(process.env.APEX_PROX_DEBOUNCE_H || '3');   // skip if re-checked within N h
const MAX        = parseInt(process.env.APEX_PROX_MAX || '5', 10);
const MODE = process.argv.includes('--recheck') ? 'recheck' : 'scan';

const entryBounds = (ez) => { const n = String(ez || '').match(/-?\d+(?:\.\d+)?/g); if (!n) return null; const v = n.map(Number).filter(x => !isNaN(x)); return v.length ? { lo: Math.min(...v), hi: Math.max(...v) } : null; };
const distPct = (px, b) => { if (px >= b.lo && px <= b.hi) return 0; const e = px < b.lo ? b.lo : b.hi; return Math.abs(px - e) / Math.abs(px) * 100; };

// Only act on a LIVE market — no point re-checking a closed FX/stock (stale price).
function marketOpen(type) {
  const d = new Date(), day = d.getUTCDay(), h = d.getUTCHours() + d.getUTCMinutes() / 60, t = (type || '').toLowerCase();
  if (t.includes('crypto')) return true;
  if (t.includes('forex'))  return !((day === 6) || (day === 0 && h < 22) || (day === 5 && h >= 22));
  if (day === 0 || day === 6) return false;          // stock/ETF: US regular hours only
  return h >= 14.5 && h < 21;
}

const STYLE_RES = {
  scalp:    { tf: '15m', expiryDays: 3,   bufferDays: 1 },
  intraday: { tf: '1h',  expiryDays: 7,   bufferDays: 2 },
  swing:    { tf: '1d',  expiryDays: 30,  bufferDays: 5 },
  position: { tf: '1d',  expiryDays: 120, bufferDays: 7 },
};
const TF_SECONDS = { '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800 };
function utcDay(ts) { return new Date(ts * 1000).toISOString().slice(0, 10); }
function verdictDir(v) {
  const u = (v || '').toUpperCase();
  if (/BUY/.test(u)) return 'long';
  if (/SELL|SHORT/.test(u)) return 'short';
  return 'neutral';
}
function rowTs(row) {
  if (row.created_at) { const t = Date.parse(row.created_at); if (!isNaN(t)) return t; }
  const m = String(row.id || '').match(/_(\d{10,})$/);
  if (m) return parseInt(m[1], 10);
  if (row.analysis_date) { const t = Date.parse(row.analysis_date); if (!isNaN(t)) return t; }
  return 0;
}
function resolutionFor(row) {
  let f = row && row.setup_features;
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
  const s = (f && f.style ? String(f.style) : 'swing').toLowerCase();
  return STYLE_RES[s] || STYLE_RES.swing;
}

function gradeRow(row, res, candles) {
  const tp = parseFloat(row.target_price), sl = parseFloat(row.stop_loss);
  if (isNaN(tp) || isNaN(sl)) return null;
  const dir = verdictDir(row.verdict);
  if (dir === 'neutral') return null;
  const entryTs = rowTs(row) / 1000;
  const tfSec = TF_SECONDS[res.tf] || 86400;
  let afterEntry = (res.tf === '1d' || res.tf === '1w')
    ? candles.filter(c => utcDay(c.time) > utcDay(entryTs))
    : candles.filter(c => c.time >= entryTs + tfSec);

  const type = row.asset_type || 'Stock';
  if (type === 'Stock' || type === 'ETF') {
    afterEntry = afterEntry.filter((c, i) => {
      const isFirstOfDay = (i === 0) || (new Date(c.time * 1000).getUTCDate() !== new Date(afterEntry[i-1].time * 1000).getUTCDate());
      return !isFirstOfDay;
    });
  }

  const eb = entryBounds(row.entry_zone);
  const scanPx = parseFloat(row.price);
  const atMarket = eb && !isNaN(scanPx) && scanPx >= eb.lo - Math.abs(eb.lo) * 0.0005 && scanPx <= eb.hi + Math.abs(eb.hi) * 0.0005;
  let filled = !eb || atMarket;
  let filledAt = filled ? entryTs : null;
  for (const bar of afterEntry) {
    if (!filled) {
      if (bar.low <= eb.hi && bar.high >= eb.lo) {
        filled = true;
        filledAt = bar.time;
      } else {
        continue;
      }
    }
    if (dir === 'short') {
      const hitTp = bar.low <= tp, hitSl = bar.high >= sl;
      if (hitTp && hitSl) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'ambiguous'; }
      if (hitTp) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'tp_hit'; }
      if (hitSl) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'sl_hit'; }
    } else {
      const hitTp = bar.high >= tp, hitSl = bar.low <= sl;
      if (hitTp && hitSl) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'ambiguous'; }
      if (hitTp) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'tp_hit'; }
      if (hitSl) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'sl_hit'; }
    }
  }
  if (filled) row.filled_at = filledAt;
  return null;
}

async function resolveAndCheckTrade(r) {
  const res = resolutionFor(r);
  const oldest = rowTs(r) / 1000;
  const buffer = res.bufferDays * 86400;
  const from = Math.floor(oldest - buffer);
  const tfSec = TF_SECONDS[res.tf] || 86400;
  const to = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
  const type = r.asset_type || 'Stock';
  
  try {
    const candleUrl = `${BASE}/api/candles?sym=${encodeURIComponent(r.symbol)}&type=${encodeURIComponent(type)}&tf=${res.tf}&from=${from}&to=${to}`;
    const candles = await fetch(candleUrl).then(res => res.json()).catch(() => null);
    if (!Array.isArray(candles) || candles.length < 2) return null;

    const graded = gradeRow(r, res, candles);
    const ageDays = (Date.now() / 1000 - oldest) / 86400;
    const resolved = graded || (ageDays > res.expiryDays ? 'expired' : null);
    
    if (resolved) {
      console.log(`[prox-watch] Auto-resolving trade ${r.id} -> ${resolved}`);
      const resolvedTime = r._resolved_at ? new Date(r._resolved_at).toISOString() : new Date().toISOString();
      await fetch(`${BASE}/api/memory`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: r.id, outcome: resolved, outcome_date: resolvedTime }),
      }).catch(err => console.error(`Failed to patch outcome for ${r.id}:`, err));
      
      r.outcome = resolved;
      return null;
    }
    
    return candles[candles.length - 1].close;
  } catch (err) {
    console.error(`Error resolving trade ${r.id}:`, err);
  }
  return null;
}

async function findClose() {
  // open=true → ALL unresolved trades regardless of age (an open position-style
  // trade can be months old and must never fall off a recent-rows window).
  const rows = await fetch(`${BASE}/api/memory?all=true&open=true&lean=true&limit=1000`).then(r => r.json()).catch(() => []);
  const open = (Array.isArray(rows) ? rows : []).filter(r =>
    (r.outcome == null || r.outcome === 'pending') && r.entry_zone && r.target_price && r.stop_loss &&
    /BUY|SELL|SHORT|LONG|WAIT|NO_EDGE|HOLD/i.test(r.verdict || ''));
  const now = Date.now(), out = [];
  for (const r of open) {
    if (!marketOpen(r.asset_type)) continue;
    const b = entryBounds(r.entry_zone); if (!b) continue;
    const px = await resolveAndCheckTrade(r); if (px == null) continue;
    const d = distPct(px, b); if (d > THRESHOLD) continue;
    let v = r.validations; if (typeof v === 'string') { try { v = JSON.parse(v); } catch { v = null; } }
    const lastTs = Array.isArray(v) && v.length ? Date.parse(v[v.length - 1].ts) : 0;
    if (now - lastTs < DEBOUNCE_H * 3600 * 1000) continue;   // debounce — don't re-check the same one repeatedly
    out.push({ id: r.id, sym: r.symbol, dist: +d.toFixed(2) });
  }
  out.sort((a, b) => a.dist - b.dist);   // closest first
  return out.slice(0, MAX);
}

async function recheck(ids) {
  const { chromium } = await import('playwright');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(`${BASE}/dashboard.html`, { waitUntil: 'load' }).catch(() => {});
  try { await page.evaluate(() => { try { localStorage.clear(); } catch {} }); } catch {}
  for (const t of ids) {
    try {
      await page.goto(`${BASE}/dashboard.html?sym=${encodeURIComponent(t.sym)}&validate=${encodeURIComponent(t.id)}&auto=1`, { waitUntil: 'load', timeout: 60000 });
      await page.waitForSelector('#analyseBtn', { timeout: 30000 });
      await page.waitForFunction(() => window.__apexValidateReady === true, undefined, { timeout: 15000 }).catch(() => {});
      await page.click('#analyseBtn');
      await page.waitForFunction(() => { const v = id => { const e = document.getElementById(id); return e && getComputedStyle(e).display !== 'none'; }; return v('resultsSection') || v('errorSection') || v('cooldownSection'); }, undefined, { timeout: 210000 });
      console.log(`  🔁 ${t.sym.padEnd(9)} re-checked (was ${t.dist}% from entry)`);
    } catch (e) { console.log(`  ✗ ${t.sym.padEnd(9)} ${(e.message || e).split('\n')[0]}`); }
    await new Promise(r => setTimeout(r, 1500));
  }
  await browser.close();
}

async function main() {
  if (MODE === 'scan') {
    const close = await findClose();
    console.log(`[prox-watch] ${close.length} trade(s) within ${THRESHOLD}% of entry${close.length ? ': ' + close.map(c => `${c.sym}(${c.dist}%)`).join(', ') : ''}`);
    if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `candidates=${JSON.stringify(close)}\ncount=${close.length}\n`);
  } else {
    let ids = []; try { ids = JSON.parse(process.env.APEX_PROX_IDS || '[]'); } catch {}
    if (!ids.length) { console.log('[prox-watch] no ids to re-check.'); return; }
    console.log(`[prox-watch] re-checking ${ids.length} close trade(s)…`);
    await recheck(ids);
    console.log('[prox-watch] done.');
  }
}
main().catch(e => { console.error('[prox-watch] fatal:', e); process.exit(1); });
