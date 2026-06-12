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

async function lastPrice(sym, type) {
  const to = Math.floor(Date.now() / 1000), from = to - 7 * 86400;
  for (const tf of ['1h', '1d']) {
    try { const c = await fetch(`${BASE}/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=${tf}&from=${from}&to=${to}`).then(r => r.json()); if (Array.isArray(c) && c.length) return c[c.length - 1].close; } catch {}
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
    const px = await lastPrice(r.symbol, r.asset_type || 'Stock'); if (px == null) continue;
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
