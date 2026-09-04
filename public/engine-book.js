// engine-book.js — Engine proof book (£100k paper experiment): hero equity,
// equity curve, book stats, open positions, closed trades.
// Data comes from /api/paper (Supabase mirror of the engine's nightly step):
//   ?table=daily&limit=500  — ascending daily equity snapshots (apex_paper_daily)
//   ?table=positions        — open engine paper positions (apex_paper_positions)
//   &book=b/c/r             — isolated challenger/champion/Book R paper states
// Closed trades are read from the latest daily row's state_extra.trades log —
// there is no separate closed-trades endpoint.

const BOOK_START_EQUITY = 100000;

// Book A = the frozen proof (Book D, seeded 16 Jul 2026); Book B = the
// challenger (certified Book H gold 252 + SPY 50d spillover gate on crypto/FX,
// seeded 10 Aug 2026 — prereg engine/data_store/pre_registration_paper_challenger_2026-08-11.md).
const BOOKS = {
  a: {
    label: 'Book A',
    currency: '£',
    startLabel: '16 Jul 2026',
    dailyTable: 'apex_paper_daily',
    positionsTable: 'apex_paper_positions',
    blurb: "The engine's forward paper-trading proof book — virtual £100,000 seeded 16 Jul 2026, stepped nightly off daily bars. Separate from the IBKR broker account; nothing here is real money.",
  },
  b: {
    label: 'Book B',
    currency: '£',
    startLabel: '10 Aug 2026',
    dailyTable: 'apex_paper_b_daily',
    positionsTable: 'apex_paper_b_positions',
    blurb: 'Challenger book — certified Book H gold 252 + SPY 50-day spillover gate on crypto/FX entries, virtual £100,000 seeded 10 Aug 2026, stepped nightly in sync with Book A as a live A/B. Paper only; nothing here is real money.',
  },
  c: {
    label: 'Book C',
    currency: '£',
    startLabel: '19 Aug 2026',
    dailyTable: 'apex_paper_c_daily',
    positionsTable: 'apex_paper_c_positions',
    blurb: 'Champion Multi-Horizon Trend Book — equal-weight blend of 63d, 126d, 252d momentum scores across 39 instruments, 0.85% maximum risk per trade, seeded 19 Aug 2026. Paper only; nothing here is real money.',
  },
  r: {
    label: 'Book R-252',
    currency: '$',
    startLabel: '27 Aug 2026',
    fallbackId: '__apex_book_r_252_forward_paper_runtime__',
    blurb: 'Active forward-paper Book R-252 — $100,000 USD, ten US-listed ETFs, month-end momentum decisions and next-session-open fills at 5 bps per side. Long-only, 95% maximum gross exposure, no leverage and no broker execution.',
  },
  f: {
    label: 'Book F (Prop Shield Elite)',
    currency: '$',
    startLabel: '28 Jul 2026',
    dailyTable: 'apex_paper_f_daily',
    positionsTable: 'apex_paper_f_positions',
    fallbackId: '__apex_book_f_prop_shield_runtime__',
    blurb: 'Forward Paper Trading (Seeded at $100,000 USD on 28 Jul 2026) — Institutional Prop Shield Engine with 100% blind cross-asset selection, rolling covariance clustering, +1.0R breakeven de-risking lock, next-session open fills, and gap-aware stop accounting. Pure forward testing tracking challenge progression from scratch to funded.',
  },
};
let _book = (new URLSearchParams(window.location.search).get('book') || 'a').toLowerCase();
if (!BOOKS[_book]) _book = 'a';

let _dailyRows = [];    // ascending daily snapshots
let _positions = [];    // open engine positions
let _eqChart = null;    // equity curve chart instance (destroyed before re-render)
let _eqSeries = null;   // equity area series instance
let _eqResize = null;   // resize handler for the equity chart (replaced, not stacked)
let _lastSyncTime = null; // latest live sync/refresh timestamp
let _latestLiveEquity = null; // latest live equity value

function setLastSyncLabel(ts = new Date()) {
  _lastSyncTime = ts;
  const label = document.getElementById('lastUpdatedLabel');
  if (label) {
    label.textContent = 'Last Sync: ' + fmtUK(ts, true);
  }
}

// ── Formatting helpers (same conventions as ibkr-trades.js) ──────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function num(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function fmtMoney(v) {
  const n = num(v);
  if (n === null) return '—';
  const sign = n < 0 ? '-' : '';
  return sign + BOOKS[_book].currency + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtSignedMoney(v) {
  const n = num(v);
  if (n === null) return '—';
  return (n >= 0 ? '+' : '-') + BOOKS[_book].currency + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtQty(v) {
  const n = num(v);
  if (n === null) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

// Paper rows carry no asset_class — classify loosely: slash pairs are forex or
// crypto (by base), anything else is a stock. Matches the engine's book shape.
function paperClassFor(inst) {
  if (inst.includes('/')) {
    const base = inst.split('/')[0].toUpperCase();
    return ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'DOGE', 'LINK', 'AVAX'].includes(base) ? 'crypto' : 'forex';
  }
  return 'stocks';
}

function fmtPrice(v, assetClass) {
  const n = num(v);
  if (n === null) return '—';
  if (assetClass === 'forex') return n.toFixed(5);
  if (assetClass === 'crypto') return n >= 100 ? n.toFixed(2) : n.toFixed(4);
  return n.toFixed(2);
}

function pnlClass(v) {
  const n = num(v);
  if (n === null || n === 0) return '';
  return n > 0 ? 'green' : 'red';
}

// Every timestamp on this page is shown in UK time (Europe/London), explicitly
// labeled "UK". Supabase stores UTC ISO strings — this is the single conversion
// point. The book steps nightly off DAILY bars, so entry dates are dates.
const UK_TZ = 'Europe/London';
function fmtUK(ts, withSeconds) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '—';
  const opts = { timeZone: UK_TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false };
  if (withSeconds) opts.second = '2-digit';
  return d.toLocaleString('en-GB', opts) + ' UK';
}

function fmtDay(ts) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-GB', { timeZone: UK_TZ, day: '2-digit', month: 'short', year: 'numeric' });
}

function setText(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (cls !== undefined) el.className = 'hs-val' + (cls ? ' ' + cls : '');
}

function dirBadge(isLong) {
  return isLong
    ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
    : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';
}

function pnlBadge(pnl, pct) {
  const pnlTxt = fmtSignedMoney(pnl) + (pct === null ? '' : ` (${pct >= 0 ? '+' : ''}${(pct * 100).toFixed(2)}%)`);
  if (pnl > 0) {
    return `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(0,200,100,0.18); color:var(--green); font-family:var(--mono); border:1px solid rgba(0,200,100,0.35); box-shadow: 0 0 10px rgba(0,200,100,0.15);">${escHtml(pnlTxt)}</span>`;
  }
  if (pnl < 0) {
    return `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(255,70,70,0.18); color:var(--red); font-family:var(--mono); border:1px solid rgba(255,70,70,0.35); box-shadow: 0 0 10px rgba(255,70,70,0.15);">${escHtml(pnlTxt)}</span>`;
  }
  return `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(255,255,255,0.06); color:var(--text2); font-family:var(--mono); border:1px solid var(--border);">${escHtml(pnlTxt)}</span>`;
}

function dirBadgeCompact(isLong) {
  return isLong
    ? '<span style="font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(0,200,100,0.12);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.25);">LONG</span>'
    : '<span style="font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(255,70,70,0.12);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.25);">SHORT</span>';
}

function pnlBadgeCompact(pnl, pct) {
  const pnlTxt = fmtSignedMoney(pnl) + (pct === null ? '' : ` (${pct >= 0 ? '+' : ''}${(pct * 100).toFixed(1)}%)`);
  const color = pnl > 0 ? 'var(--green)' : (pnl < 0 ? 'var(--red)' : 'var(--text2)');
  const bg = pnl > 0 ? 'rgba(0,200,100,0.12)' : (pnl < 0 ? 'rgba(255,70,70,0.12)' : 'rgba(255,255,255,0.04)');
  const border = pnl > 0 ? 'rgba(0,200,100,0.25)' : (pnl < 0 ? 'rgba(255,70,70,0.25)' : 'var(--border)');
  return `<span style="display:inline-block; font-size:11px; font-weight:600; padding:2px 6px; border-radius:4px; background:${bg}; color:${color}; font-family:var(--mono); border:1px solid ${border}; white-space:nowrap;">${escHtml(pnlTxt)}</span>`;
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadEngineBook() {
  const SUPA_URL = 'https://cuvchjhaojhmxfgczndy.supabase.co';
  const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
  const supaHeaders = { 'apikey': SUPA_ANON, 'Authorization': `Bearer ${SUPA_ANON}` };
  const tables = BOOKS[_book];

  let daily = null;
  let positions = null;

  try {
    const [dRes, pRes] = await Promise.all([
      fetch(`/api/paper?table=daily&limit=500&book=${_book}`).catch(() => null),
      fetch(`/api/paper?table=positions&limit=100&book=${_book}`).catch(() => null),
    ]);
    if (dRes && dRes.ok) daily = await dRes.json().catch(() => null);
    if (pRes && pRes.ok) positions = await pRes.json().catch(() => null);
  } catch (e) { /* fall through to direct Supabase */ }

  // Direct namespaced mirror fallback for Book R and Book F (including static local dev).
  if ((_book === 'r' || _book === 'f') && (!Array.isArray(daily) || !Array.isArray(positions))) {
    const fallbackId = tables.fallbackId || (_book === 'f' ? '__apex_book_f_prop_shield_runtime__' : '__apex_book_r_252_forward_paper_runtime__');
    const stateRes = await fetch(
      `${SUPA_URL}/rest/v1/apex_analyses?id=eq.${fallbackId}&select=feature_vector&limit=1`,
      { headers: supaHeaders },
    ).catch(() => null);
    if (stateRes && stateRes.ok) {
      const stateRows = await stateRes.json().catch(() => []);
      const payload = stateRows[0] && stateRows[0].feature_vector;
      if (!Array.isArray(daily) && payload && Array.isArray(payload.daily)) daily = payload.daily;
      if (!Array.isArray(positions) && payload && Array.isArray(payload.positions)) positions = payload.positions;
    }
  }

  // Direct Supabase REST fallback if the Vercel serverless proxy route returned
  // an error or is unreachable (e.g. static local dev server without /api).
  if (_book !== 'r' && (!Array.isArray(daily) || !Array.isArray(positions)) && tables.dailyTable) {
    const [sdRes, spRes] = await Promise.all([
      fetch(`${SUPA_URL}/rest/v1/${tables.dailyTable}?order=date.asc&limit=500`, { headers: supaHeaders }).catch(() => null),
      fetch(`${SUPA_URL}/rest/v1/${tables.positionsTable}?order=instrument.asc&limit=100`, { headers: supaHeaders }).catch(() => null),
    ]);
    if (sdRes && sdRes.ok) daily = await sdRes.json().catch(() => daily);
    if (spRes && spRes.ok) positions = await spRes.json().catch(() => positions);
  }

  // Committed local engine snapshot fallback (Books C and F).
  if ((_book === 'c' || _book === 'f') && (!Array.isArray(daily) || !Array.isArray(positions))) {
    const snapshotUrl = _book === 'f' ? '/book-f-paper-snapshot.json' : '/book-c-paper-snapshot.json';
    const fallback = await fetch(snapshotUrl, { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .catch(() => null);
    if (fallback) {
      if (!Array.isArray(daily) && Array.isArray(fallback.daily)) daily = fallback.daily;
      if (!Array.isArray(positions) && Array.isArray(fallback.positions)) positions = fallback.positions;
    }
  }

  if (Array.isArray(daily)) _dailyRows = daily;
  if (Array.isArray(positions)) _positions = positions;

  renderAll();
}

// ── Derived book state ───────────────────────────────────────────────────────
function latestDaily() {
  return _dailyRows.length ? _dailyRows[_dailyRows.length - 1] : null;
}

function closedTrades() {
  const latest = latestDaily();
  const trades = latest && latest.state_extra && Array.isArray(latest.state_extra.trades)
    ? latest.state_extra.trades
    : [];
  return [...trades].sort((a, b) => String(b.exit_time || '').localeCompare(String(a.exit_time || '')));
}

let _gbpUsdRate = 1.285;

function calcTradePnl(inst, entry, currentPx, units, isLong, gbpusd = (_gbpUsdRate || 1.285)) {
  if (entry === null || currentPx === null || units === null || entry <= 0 || currentPx <= 0) return null;
  const rawDiff = (isLong ? (currentPx - entry) : (entry - currentPx)) * units;
  const cls = paperClassFor(inst);

  let pnlUsd = rawDiff;
  if (cls === 'forex' && inst.includes('/')) {
    if (inst.startsWith('USD/')) {
      pnlUsd = rawDiff / currentPx;
    } else if (inst.endsWith('/USD')) {
      pnlUsd = rawDiff;
    }
  }

  if (_book === 'r' || _book === 'f') return pnlUsd;

  // Convert USD to Book Currency (£ GBP) for Books A/B/C.
  return pnlUsd / (gbpusd || 1.285);
}

function positionUpnl(p) {
  const entry = num(p.entry_price);
  const lastPx = num(p.last_px);
  const units = num(p.units);
  if (entry === null || lastPx === null || units === null) return null;
  const isLong = String(p.direction || '').toLowerCase() !== 'short';
  return calcTradePnl(String(p.instrument || ''), entry, lastPx, units, isLong, _gbpUsdRate);
}

// ── Hero + book stats ────────────────────────────────────────────────────────
function renderHero() {
  const latest = latestDaily();
  const actualStartLabel = _dailyRows.length && _dailyRows[0].date
    ? fmtDay(_dailyRows[0].date)
    : BOOKS[_book].startLabel;

  const equity = latest ? num(latest.equity) : BOOK_START_EQUITY;
  const dayPnl = latest ? num(latest.day_pnl) : 0;
  const cumPnl = latest && num(latest.cum_pnl) !== null
    ? num(latest.cum_pnl)
    : (equity !== null ? equity - BOOK_START_EQUITY : 0);
  const cash = latest ? num(latest.cash) : BOOK_START_EQUITY;

  let maxDD = 0;
  for (const r of _dailyRows) {
    const dd = num(r.drawdown_from_peak);
    if (dd !== null && (maxDD === null || dd > maxDD)) maxDD = dd;
  }
  const curDD = latest ? (num(latest.drawdown_from_peak) || 0) : 0;

  setText('engEquity', fmtMoney(equity));
  setText('engCash', fmtMoney(cash));
  setText('engDayPnl', fmtSignedMoney(dayPnl), pnlClass(dayPnl));
  setText('engCumPnl', fmtSignedMoney(cumPnl), pnlClass(cumPnl));
  setText('engCurDD', curDD > 0 ? '-' + (curDD * 100).toFixed(2) + '%' : '0.00%', curDD > 0 ? 'red' : '');
  setText('engMaxDD', maxDD > 0 ? '-' + (maxDD * 100).toFixed(2) + '%' : '0.00%', maxDD > 0 ? 'red' : '');

  const dayChip = document.getElementById('engDayChip');
  if (dayChip) {
    dayChip.textContent = 'Today: ' + fmtSignedMoney(dayPnl);
    dayChip.style.color = (dayPnl >= 0 ? 'var(--green)' : 'var(--red)');
  }
  const sinceChip = document.getElementById('engSinceChip');
  if (sinceChip) {
    const pct = (cumPnl / BOOK_START_EQUITY) * 100;
    sinceChip.textContent = `Net Return: ${fmtSignedMoney(cumPnl)} (${cumPnl >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
    sinceChip.style.color = cumPnl >= 0 ? 'var(--green)' : 'var(--red)';
  }

  const heroLine = document.getElementById('engHeroLine');
  if (heroLine) {
    const se = (latest && latest.state_extra) || {};
    const bits = [];
    if (latest && latest.date) bits.push('snapshot ' + latest.date);
    if (num(se.peak) !== null) bits.push('peak ' + fmtMoney(se.peak));
    bits.push((_dailyRows.length || 0) + ' daily snapshots since ' + actualStartLabel);
    if (se.halted) bits.push('HALTED — drawdown rule hit');
    heroLine.textContent = bits.join(' · ');
    heroLine.style.color = se.halted ? 'var(--red)' : 'var(--text3)';
  }
  const since = document.getElementById('engSinceSub');
  if (since) since.textContent = 'since ' + actualStartLabel;

  const label = document.getElementById('lastUpdatedLabel');
  if (label) {
    const ts = _lastSyncTime || (_positions[0] && _positions[0].updated_at) || (latest && latest.inserted_at) || new Date();
    label.textContent = 'Last Sync: ' + fmtUK(ts, true);
  }
}

// ── Forward Testing Progress Tracker from 100k ──────────────────────────────
function renderProgressTracker(liveOpenPnl = null, officialOpenPnl = null) {
  const card = document.getElementById('engProgressCard');
  if (!card) return;

  const meta = BOOKS[_book] || {};
  const seed = meta.seed || 100000;
  const curr = meta.currency || '$';
  const latest = latestDaily();

  const officialEquity = latest ? num(latest.equity) : seed;
  const closed = closedTrades();
  const realizedBanked = closed.reduce((s, t) => s + (num(t.pnl) || 0), 0);

  // Compute live open P&L across all active positions
  let calcOpenPnl = 0;
  let activePositions = [];
  for (const p of _positions) {
    if (!p || num(p.units) <= 0) continue;
    activePositions.push(p);
    const inst = String(p.instrument || '');
    const m = _liveMarks[inst];
    const px = m ? m.px : num(p.last_px);
    const entry = num(p.entry_price);
    const units = num(p.units);
    if (entry !== null && units !== null && px !== null) {
      const isLong = String(p.direction || '').toLowerCase() !== 'short';
      const pnl = calcTradePnl(inst, entry, px, units, isLong, _gbpUsdRate);
      if (pnl !== null) calcOpenPnl += pnl;
    }
  }

  const effectiveOpenPnl = (liveOpenPnl !== null) ? liveOpenPnl : calcOpenPnl;
  const effectiveOfficialOpenPnl = (officialOpenPnl !== null) ? officialOpenPnl : 0;

  const liveFloatingEquity = (_book === 'r' || _book === 'f')
    ? (officialEquity + (effectiveOpenPnl - effectiveOfficialOpenPnl))
    : (seed + realizedBanked + effectiveOpenPnl);

  const totalNetGrowth = liveFloatingEquity - seed;
  const growthPct = (totalNetGrowth / seed) * 100;

  // Max Drawdown limit floor (10% from starting seed)
  const ddFloor = seed * 0.90;
  const cushion = liveFloatingEquity - ddFloor;

  // Update text nodes
  const titleEl = document.getElementById('progTitle');
  if (titleEl) {
    titleEl.textContent = `${meta.currency === '$' ? '$100K' : '£100K'} Forward Testing Progress (${meta.label.split('·')[0].trim()})`;
  }
  const baseNote = document.getElementById('progBaseNote');
  if (baseNote) {
    baseNote.textContent = `Seeded at ${curr}${seed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${meta.currency === '$' ? 'USD' : 'GBP'}`;
  }
  const startVal = document.getElementById('progStartVal');
  if (startVal) startVal.textContent = fmtMoney(seed);
  
  const liveVal = document.getElementById('progLiveVal');
  if (liveVal) liveVal.textContent = fmtMoney(liveFloatingEquity);
  
  const liveValSub = document.getElementById('progLiveValSub');
  if (liveValSub) {
    liveValSub.textContent = activePositions.length
      ? `includes ${fmtSignedMoney(effectiveOpenPnl)} floating mark`
      : 'all cash (flat position)';
  }

  const growthVal = document.getElementById('progGrowthVal');
  if (growthVal) {
    growthVal.textContent = fmtSignedMoney(totalNetGrowth);
    growthVal.className = 'hs-val ' + (totalNetGrowth >= 0 ? 'green' : 'red');
  }
  const growthPctEl = document.getElementById('progGrowthPct');
  if (growthPctEl) {
    growthPctEl.textContent = `${totalNetGrowth >= 0 ? '+' : ''}${growthPct.toFixed(2)}% net forward return`;
    growthPctEl.style.color = totalNetGrowth >= 0 ? 'var(--green)' : 'var(--red)';
  }

  const openPnlEl = document.getElementById('progOpenPnl');
  if (openPnlEl) {
    openPnlEl.textContent = fmtSignedMoney(effectiveOpenPnl);
    openPnlEl.className = 'hs-val ' + (effectiveOpenPnl > 0 ? 'green' : (effectiveOpenPnl < 0 ? 'red' : ''));
  }
  const openSubEl = document.getElementById('progOpenSub');
  if (openSubEl) {
    openSubEl.textContent = `${activePositions.length} active position${activePositions.length === 1 ? '' : 's'} moving equity`;
  }

  const cushionVal = document.getElementById('progCushionVal');
  if (cushionVal) {
    cushionVal.textContent = fmtSignedMoney(cushion);
    cushionVal.className = 'hs-val ' + (cushion > 0 ? 'green' : 'red');
  }

  // Milestones
  const p1Target = seed * 1.08;
  const p2Target = seed * 1.134;

  // Bar width calculation (from 0% baseline to funded)
  let fillPct = 0;
  if (growthPct <= 0) {
    fillPct = 0;
  } else if (growthPct < 8.0) {
    fillPct = (growthPct / 8.0) * 45;
  } else if (growthPct < 13.4) {
    fillPct = 45 + ((growthPct - 8.0) / 5.4) * 35;
  } else {
    fillPct = Math.min(100, 80 + ((growthPct - 13.4) / 10.0) * 20);
  }

  const barFill = document.getElementById('progBarFill');
  if (barFill) barFill.style.width = fillPct.toFixed(1) + '%';

  const targetPctEl = document.getElementById('progTargetPct');
  if (targetPctEl) {
    if (growthPct >= 13.4) {
      targetPctEl.innerHTML = `<span style="color:var(--green); font-weight:700;">✓ Phase 1 &amp; 2 Passed</span> · <span style="color:#A78BFA; font-weight:700;">Funded Account Active (${fmtSignedMoney(totalNetGrowth)})</span>`;
    } else if (growthPct >= 8.0) {
      const p2Gain = totalNetGrowth - (seed * 0.08);
      const p2TargetGain = seed * 0.054;
      const p2Pct = Math.min(100, (p2Gain / p2TargetGain) * 100);
      const remainingP2 = Math.max(0, p2TargetGain - p2Gain);
      targetPctEl.innerHTML = `<span style="color:var(--green); font-weight:700;">✓ Phase 1 Passed (+8%)</span> · <span style="color:#38BDF8; font-weight:700;">Phase 2: ${p2Pct.toFixed(1)}%</span> · <span style="color:var(--text2);">${fmtMoney(remainingP2)} to Funded</span>`;
    } else if (growthPct > 0) {
      const remainingP1 = Math.max(0, (seed * 0.08) - totalNetGrowth);
      const p1Pct = Math.min(100, (totalNetGrowth / (seed * 0.08)) * 100);
      targetPctEl.innerHTML = `<span style="color:#F5B04C; font-weight:700;">Phase 1: ${p1Pct.toFixed(1)}% Completed</span> · <span style="color:var(--text2);">${fmtMoney(remainingP1)} to pass (+8%)</span>`;
    } else {
      targetPctEl.innerHTML = `<span style="color:#F5B04C; font-weight:700;">Phase 1 Challenge Active</span> · <span style="color:var(--text2);">${fmtMoney(seed * 0.08)} Profit Target (+8.0%)</span>`;
    }
  }

  const labelsEl = document.getElementById('progMilestoneLabels');
  if (labelsEl) {
    const isP1Done = growthPct >= 8.0;
    const isP2Done = growthPct >= 13.4;

    const p1Badge = isP1Done
      ? `<span style="font-size:9.5px; background:rgba(52,211,153,0.15); color:var(--green); padding:1px 5px; border-radius:3px; font-weight:700;">PASSED ✓</span>`
      : `<span style="font-size:9.5px; background:rgba(245,176,76,0.15); color:#F5B04C; padding:1px 5px; border-radius:3px; font-weight:600;">IN PROGRESS</span>`;
    const p1Col = isP1Done ? 'var(--green)' : '#F5B04C';

    const p2Badge = isP2Done
      ? `<span style="font-size:9.5px; background:rgba(52,211,153,0.15); color:var(--green); padding:1px 5px; border-radius:3px; font-weight:700;">PASSED ✓</span>`
      : (isP1Done ? `<span style="font-size:9.5px; background:rgba(56,189,248,0.15); color:#38BDF8; padding:1px 5px; border-radius:3px; font-weight:600;">IN PROGRESS</span>` : `<span style="font-size:9.5px; background:rgba(255,255,255,0.04); color:var(--text3); padding:1px 5px; border-radius:3px;">LOCKED</span>`);
    const p2Col = isP2Done ? 'var(--green)' : (isP1Done ? '#38BDF8' : 'var(--text3)');

    const fundedBadge = isP2Done
      ? `<span style="font-size:9.5px; background:rgba(167,139,250,0.18); color:#A78BFA; padding:1px 5px; border-radius:3px; font-weight:700;">ACTIVE ● PAYOUTS</span>`
      : `<span style="font-size:9.5px; background:rgba(255,255,255,0.04); color:var(--text3); padding:1px 5px; border-radius:3px;">LOCKED</span>`;
    const fundedCol = isP2Done ? '#A78BFA' : 'var(--text3)';

    labelsEl.innerHTML = `
      <div><span style="color: var(--text2); font-weight: 600;">${curr}${(seed / 1000).toFixed(0)}k</span> Baseline</div>
      <div><span style="color: ${p1Col}; font-weight: 600;">${curr}${(p1Target / 1000).toFixed(0)}k (+8%)</span> Phase 1 ${p1Badge}</div>
      <div><span style="color: ${p2Col}; font-weight: 600;">${curr}${(p2Target / 1000).toFixed(1)}k (+5%)</span> Phase 2 ${p2Badge}</div>
      <div><span style="color: ${fundedCol}; font-weight: 600;">${curr}${(p2Target / 1000).toFixed(1)}k+</span> Funded Stage ${fundedBadge}</div>
    `;
  }

  // Open trades callout banner
  const openDesc = document.getElementById('progOpenTradeDesc');
  const openImpact = document.getElementById('progOpenTradeImpact');
  if (openDesc && openImpact) {
    if (activePositions.length > 0) {
      const parts = activePositions.map(p => {
        const inst = String(p.instrument || '');
        const m = _liveMarks[inst];
        const px = m ? m.px : num(p.last_px);
        const entry = num(p.entry_price);
        const units = num(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        const pnl = calcTradePnl(inst, entry, px, units, isLong, _gbpUsdRate);
        const isBe = num(p.stop) !== null && entry !== null && Math.abs(num(p.stop) - entry) < 0.05;
        const stopTxt = isBe ? 'Stop @ Breakeven (0 Risk)' : ('Stop ' + (p.stop ? num(p.stop).toFixed(2) : '—'));
        return `<strong>${escHtml(inst)} ${isLong ? 'Long' : 'Short'}</strong> (${fmtSignedMoney(pnl || 0)} live floating mark · ${stopTxt})`;
      });
      openDesc.innerHTML = `<span>⚡ <strong>Live Open Position Shifting Progress:</strong> ${parts.join(' · ')}</span>`;
      
      const bankedOnOpen = activePositions.reduce((s, p) => s + (num(p.realized_pnl_total) || 0), 0);
      openImpact.innerHTML = `<span style="color:var(--green);">${fmtSignedMoney(effectiveOpenPnl)} live mark</span>${bankedOnOpen > 0 ? ' · +' + fmtMoney(bankedOnOpen) + ' banked partials (1:1 R:R / +1.0R)' : ''}`;
    } else {
      openDesc.innerHTML = `<span>✓ <strong>All Positions Flat:</strong> Capital safely preserved in cash (${fmtMoney(liveFloatingEquity)}) · Awaiting next systematic entry signal.</span>`;
      openImpact.textContent = '0 Open Risk';
    }
  }
}

function renderBookStats() {
  const latest = latestDaily();

  const openCount = _positions.length || (latest ? num(latest.n_open) : 0) || 0;

  let gross = 0;
  let unreal = 0;
  let unrealKnown = false;
  for (const p of _positions) {
    const lastPx = num(p.last_px);
    const units = num(p.units);
    if (lastPx !== null && units !== null) gross += Math.abs(lastPx * units);
    const u = positionUpnl(p);
    if (u !== null) { unreal += u; unrealKnown = true; }
  }
  if (!_positions.length && latest && num(latest.gross_exposure_x) !== null && num(latest.equity) !== null) {
    gross = num(latest.gross_exposure_x) * num(latest.equity);
  }
  const equity = latest ? num(latest.equity) : BOOK_START_EQUITY;
  const grossX = (equity !== null && equity > 0) ? gross / equity : 0;

  setText('engOpenCount', String(openCount));
  setText('engGross', fmtMoney(gross));
  const gxEl = document.getElementById('engGrossX');
  if (gxEl) gxEl.textContent = grossX === 0 ? '0.00x of book equity' : grossX.toFixed(2) + 'x of book equity';
  setText('engUnreal', unrealKnown ? fmtSignedMoney(unreal) : fmtMoney(0), unrealKnown ? pnlClass(unreal) : '');

  const closed = closedTrades();
  const wins = closed.filter(t => (num(t.pnl) || 0) > 0);
  const winRate = closed.length ? (wins.length / closed.length) * 100 : null;
  const realized = closed.reduce((acc, t) => acc + (num(t.pnl) || 0), 0);

  setText('engWinRate', winRate === null ? '—' : winRate.toFixed(1) + '%');
  const ccEl = document.getElementById('engClosedCount');
  if (ccEl) ccEl.textContent = `${closed.length} closed`;
  setText('engRealized', closed.length ? fmtSignedMoney(realized) : fmtMoney(0), closed.length ? pnlClass(realized) : '');
}

// ── Equity curve ─────────────────────────────────────────────────────────────
function renderEquityChart() {
  const chartEl = document.getElementById('equityChart');
  const emptyEl = document.getElementById('equityChartEmpty');
  if (!chartEl) return;

  if (_eqChart) { try { _eqChart.remove(); } catch (e) {} _eqChart = null; _eqSeries = null; }

  const pts = [];
  const seen = new Set();
  for (const r of _dailyRows) {
    const eq = num(r.equity);
    if (!r.date || eq === null || seen.has(r.date)) continue;
    seen.add(r.date);
    pts.push({ time: r.date, value: eq });
  }

  if (_latestLiveEquity !== null && pts.length) {
    const todayStr = new Date().toISOString().slice(0, 10);
    if (pts[pts.length - 1].time === todayStr) {
      pts[pts.length - 1].value = _latestLiveEquity;
    } else {
      pts.push({ time: todayStr, value: _latestLiveEquity });
    }
  }

  if (pts.length < 2 || typeof LightweightCharts === 'undefined') {
    chartEl.style.display = 'none';
    if (emptyEl) {
      emptyEl.style.display = 'flex';
      emptyEl.textContent = _book === 'c'
        ? 'Book C is seeded at £100,000.00 with the promoted 0.85% risk budget. Waiting for the next verified daily equity snapshot.'
        : (_book === 'f'
          ? 'Book F (Prop Shield Elite) is active at $100,000.00. Waiting for the next closed-session snapshot.'
          : (_book === 'r'
            ? 'Book R-252 is active at $100,000.00. Waiting for the next closed-session snapshot.'
            : 'Waiting for daily equity snapshots…'));
    }
    return;
  }
  chartEl.style.display = '';
  if (emptyEl) emptyEl.style.display = 'none';

  const up = pts[pts.length - 1].value >= pts[0].value;
  const line = up ? '#34D399' : '#F87171';
  const chart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth,
    height: chartEl.clientHeight || 300,
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#64748B',
      fontFamily: "'Space Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: { color: 'rgba(51, 65, 85, 0.35)' },
      horzLines: { color: 'rgba(51, 65, 85, 0.35)' },
    },
    rightPriceScale: { borderColor: 'rgba(51, 65, 85, 0.6)' },
    timeScale: { borderColor: 'rgba(51, 65, 85, 0.6)', timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    localization: { priceFormatter: v => fmtMoney(v) },
  });
  const area = chart.addAreaSeries({
    lineColor: line,
    lineWidth: 2,
    topColor: up ? 'rgba(52, 211, 153, 0.22)' : 'rgba(248, 113, 113, 0.22)',
    bottomColor: 'rgba(0, 0, 0, 0)',
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
  });
  area.setData(pts);
  // Seed reference line at 100,000 in the selected book's account currency.
  area.createPriceLine({ price: BOOK_START_EQUITY, color: 'rgba(56, 189, 248, 0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'seed' });
  chart.timeScale().fitContent();
  if (_eqResize) window.removeEventListener('resize', _eqResize);
  _eqResize = () => {
    if (chartEl.isConnected) chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight || 300 });
  };
  window.addEventListener('resize', _eqResize);
  _eqChart = chart;
  _eqSeries = area;
}

// ── Open positions grid ──────────────────────────────────────────────────────
// +1R partial/breakeven trigger: entry ± |entry − initial stop|, direction-aware.
function partialsInfo(entry, lastPx, isLong, cls, tmsP1, initStop, rawStop) {
  if (entry === null) return { targetTxt: '—', distTxt: '', color: 'var(--text3)' };

  let riskDist = (initStop !== null && Math.abs(entry - initStop) > 0.05)
    ? Math.abs(entry - initStop)
    : (rawStop !== null ? Math.abs(entry - rawStop) : 0);
  if (riskDist <= 0) return { targetTxt: '—', distTxt: '', color: 'var(--text3)' };

  const partialTarget = isLong ? entry + riskDist : entry - riskDist;
  const targetTxt = fmtPrice(partialTarget, cls);
  const isHit = (tmsP1 === true) || (lastPx !== null && (isLong ? lastPx >= partialTarget : lastPx <= partialTarget));
  if (isHit) return { targetTxt, distTxt: '(Hit ✅)', color: 'var(--green)' };

  if (lastPx !== null) {
    const dist = isLong ? partialTarget - lastPx : lastPx - partialTarget;
    if (dist <= 0) return { targetTxt, distTxt: '(Hit ✅)', color: 'var(--green)' };
    return { targetTxt, distTxt: `(${fmtPrice(dist, cls)} away)`, color: '#F5B04C' };
  }
  return { targetTxt, distTxt: '', color: '#F5B04C' };
}

function renderPositionCard(p) {
  const inst = String(p.instrument || '');
  const cls = paperClassFor(inst);
  const isLong = String(p.direction || '').toLowerCase() !== 'short';

  const units = num(p.units);
  const entry = num(p.entry_price);
  const stop = num(p.stop);
  const target = num(p.target);
  const lastPx = num(p.last_px);
  const upnl = positionUpnl(p);
  const upnlCls = upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''));
  const hasPartials = p.tms_p1 === true || p.partial_taken === true || (num(p.realized_pnl_total) > 0);
  const pInfo = partialsInfo(entry, lastPx, isLong, cls, hasPartials, num(p.initial_stop), stop);

  const enteredTxt = p.entry_time ? fmtDay(p.entry_time) : '—';
  const updated = p.updated_at ? fmtUK(p.updated_at) : '—';
  const bars = num(p.bars_open);
  const bankedPartials = num(p.realized_pnl_total);
  const nextRebalanceTxt = p.next_rebalance_date ? fmtDay(p.next_rebalance_date) : 'next official month-end';
  const exitRows = _book === 'r' ? `
      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Stop Loss</span>
        <span style="font-family: var(--mono); color: var(--text3);">Not used</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; white-space: nowrap;">
        <span style="color: var(--text3)">Partials (+1.0R)</span>
        <span style="font-family: var(--mono); color: var(--text3);">Not used</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
        <span style="color: var(--text3)">Take Profit</span>
        <span style="font-family: var(--mono); color: var(--text3);">Not used</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 10px; color: var(--text3); font-family: var(--mono);">
        <span>Next month-end review</span>
        <span>${escHtml(nextRebalanceTxt)} close</span>
      </div>` : `
      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Stop Loss</span>
        <span style="font-family: var(--mono); color: var(--red);">${escHtml(stop !== null ? fmtPrice(stop, cls) : '—')}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; white-space: nowrap;">
        <span style="color: var(--text3)">Partials (+1.0R / 1:1 R:R)</span>
        <span style="font-family: var(--mono); color: ${pInfo.color}; font-weight: 600;">${escHtml(pInfo.targetTxt)} <span style="font-size:11px;font-weight:500;">${escHtml(pInfo.distTxt)}</span></span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
        <span style="color: var(--text3)">Take Profit</span>
        <span style="font-family: var(--mono); color: var(--green);">${escHtml(target !== null ? fmtPrice(target, cls) : '—')}</span>
      </div>`;

  const isRiskFree = hasPartials || (entry !== null && stop !== null && (isLong ? stop >= entry - 0.01 : stop <= entry + 0.01));

  // Visual Risk-Reward Gauge
  let gaugeHtml = '';
  if (entry !== null && stop !== null && lastPx !== null) {
    const initStop = num(p.initial_stop) || stop;
    const initialRisk = Math.abs(entry - initStop);
    if (initialRisk > 0.001) {
      const rVal = isLong ? (lastPx - entry) / initialRisk : (entry - lastPx) / initialRisk;
      // Gauge scale spans -1.0R to +2.0R (range of 3.0R)
      const gaugePct = Math.min(100, Math.max(0, ((rVal - (-1.0)) / 3.0) * 100));
      const entryPct = (1.0 / 3.0) * 100; // 33.3%
      const rColor = rVal >= 0 ? 'var(--green)' : 'var(--red)';
      gaugeHtml = `
        <div style="margin: 10px 0 6px 0; background: rgba(0,0,0,0.25); padding: 9px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05);">
          <div style="display: flex; justify-content: space-between; font-size: 10px; font-family: var(--mono); color: var(--text3); margin-bottom: 5px;">
            <span style="color: var(--red);">Stop -1.0R</span>
            <span style="color: var(--text2);">Entry 0.0R</span>
            <span style="color: var(--green);">Target +2.0R</span>
          </div>
          <div class="pos-gauge-track" style="position: relative; height: 6px; background: rgba(255,255,255,0.08); border-radius: 999px; overflow: visible;">
            <div style="position: absolute; left: ${entryPct.toFixed(1)}%; top: -2px; bottom: -2px; width: 2px; background: rgba(255,255,255,0.3); z-index: 2;" title="Entry Price"></div>
            <div class="pos-gauge-fill" style="width: ${gaugePct.toFixed(1)}%; height: 100%; border-radius: 999px; background: ${rColor}; opacity: 0.85;"></div>
            <div class="pos-gauge-marker" style="position: absolute; left: calc(${gaugePct.toFixed(1)}% - 5px); top: -3px; width: 12px; height: 12px; border-radius: 50%; background: #FFFFFF; border: 2px solid ${rColor}; box-shadow: 0 0 8px ${rColor}; z-index: 3;" title="Current: ${rVal >= 0 ? '+' : ''}${rVal.toFixed(2)}R"></div>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 10.5px; font-family: var(--mono); margin-top: 6px;">
            <span style="color: var(--text3);">${escHtml(stop !== null ? fmtPrice(stop, cls) : '—')}</span>
            <span style="color: ${rColor}; font-weight: 700; background: rgba(255,255,255,0.04); padding: 1px 6px; border-radius: 4px;">${rVal >= 0 ? '+' : ''}${rVal.toFixed(2)}R</span>
            <span style="color: var(--green);">${escHtml(target !== null ? fmtPrice(target, cls) : '—')}</span>
          </div>
        </div>
      `;
    }
  }

  return `
    <div class="stat-item ibkr-pos-card eng-pos-card" data-instrument="${escHtml(inst)}" data-live-entry="${entry}" data-live-units="${units}" data-live-dir="${isLong ? 'long' : 'short'}" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s;">
      <div class="card-face-stats">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
          <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${escHtml(inst)}</strong>
          ${dirBadge(isLong)}
          ${isRiskFree ? '<span class="badge-status-riskfree">🛡️ RISK-FREE (BE)</span>' : ''}
        </div>
        <span style="font-size: 11px; font-weight: 700; color: var(--text3); font-family: var(--mono);">${escHtml(fmtQty(units))} units</span>
      </div>

      <div style="font-size: 10.5px; color: var(--text3); font-family: var(--mono); margin-top: 2px;">Entered: ${escHtml(enteredTxt)}${bars !== null ? ` · ${bars} bar${bars === 1 ? '' : 's'} open` : ''}</div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-top: 4px;">
        <span style="color: var(--text3)">Avg Entry</span>
        <span style="font-family: var(--mono); color: var(--text2);">${escHtml(fmtPrice(entry, cls))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Last Price</span>
        <span class="card-last-px" style="font-family: var(--mono); color: var(--text2); font-weight: 600;">${escHtml(fmtPrice(lastPx, cls))}</span>
      </div>

      ${exitRows}

      ${gaugeHtml}

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
        <span style="color: var(--text)">Unrealized P&amp;L</span>
        <span class="card-upnl-val ${upnlCls}" style="font-family: var(--mono); font-size: 16px;">${escHtml(upnl === null ? '—' : fmtSignedMoney(upnl))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin-top: 2px;">
        <span style="color: var(--text3); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;">Live Intraday</span>
        <span class="live-mark-val" style="font-family: var(--mono); font-size: 11.5px; color: var(--text3);">—</span>
      </div>

      ${bankedPartials !== null && bankedPartials !== 0 ? `
      <div class="pos-shield-card">
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
          <span style="color: var(--text2); font-weight: 600; display: inline-flex; align-items: center; gap: 5px;">
            <span>🛡️</span> Banked partials (+1.0R)
          </span>
          <span class="${bankedPartials > 0 ? 'pos' : 'neg'}" style="font-family: var(--mono); font-size: 13px; font-weight: 700;">${escHtml(fmtSignedMoney(bankedPartials))}</span>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: var(--green); font-family: var(--mono); margin-top: 4px;">
          <span>Execution Lock</span>
          <span style="font-weight: 700; background: rgba(47,214,163,0.15); padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(47,214,163,0.3);">1:1 R:R (+1.0R secured)</span>
        </div>
        <div style="font-size: 10.5px; color: var(--text3); margin-top: 5px; line-height: 1.4;">
          50% size locked into cash · Stop automatically anchored to entry ($0 downside risk)
        </div>
      </div>` : ''}

      <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
        Engine book · ${escHtml(updated)}
      </div>
      </div>
    </div>
  `;
}

let _activeClass = 'all';

function setClass(cls) {
  _activeClass = cls || 'all';
  for (const btn of document.querySelectorAll('.vt-btn[data-class]')) {
    btn.classList.toggle('active', btn.dataset.class === _activeClass);
  }
  renderPositions();
}

function initClassToggle() {
  for (const btn of document.querySelectorAll('.vt-btn[data-class]')) {
    btn.addEventListener('click', () => setClass(btn.dataset.class));
  }
}

function renderPositions() {
  const wrap = document.getElementById('engPositionsWrap');
  if (!wrap) return;

  const latest = latestDaily();
  const noteEl = document.getElementById('engPosNote');
  if (noteEl) {
    noteEl.textContent = latest && latest.notes ? `last step: ${latest.notes}` : '';
  }

  let open = _positions.filter(p => p && p.instrument && num(p.units) > 0 && String(p.status || '').toLowerCase() !== 'closed');
  if (_activeClass !== 'all') {
    open = open.filter(p => paperClassFor(String(p.instrument || '')) === _activeClass);
  }

  if (!open.length) {
    const bookLabel = BOOKS[_book].label;
    const classLabel = _activeClass === 'all' ? '' : _activeClass + ' ';
    const pending = latest && latest.state_extra && latest.state_extra.pending
      ? Object.entries(latest.state_extra.pending)
      : [];
    const pendingMsg = pending.length
      ? `<div style="margin-top:14px; padding: 12px 18px; border-radius: 8px; background: rgba(56,189,248,0.08); border: 1px solid rgba(56,189,248,0.25); display: inline-block; font-style: normal; color: #38BDF8; font-size: 13px; font-family: 'Space Mono', monospace;">
          ⚡ <strong>${pending.length} Pending Entry Order${pending.length === 1 ? '' : 's'} Queued for the Next Session Open</strong><br/>
          <span style="color: var(--text2); font-size: 11.5px; display: block; margin-top: 4px;">${pending.map(([inst, d]) => `${escHtml(inst)} (${escHtml((((d || {}).pos || {}).direction || '—'))})`).join(', ')}</span>
        </div>`
      : '';
    const subNote = _book === 'f'
      ? `<br/><span style="font-size: 12px; color: var(--text3); display: block; margin-top: 6px; font-style: normal;">All capital safely preserved in cash (${fmtMoney(BOOK_START_EQUITY)} USD). Quantitative multi-horizon scanner will execute entry signals during the nightly session.</span>`
      : '';
    wrap.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">
      No open ${classLabel}positions for ${bookLabel}.${subNote}
      ${pendingMsg}
    </div>`;
    return;
  }

  wrap.innerHTML = open.map(renderPositionCard).join('');
  applyLiveMarks();
  refreshLiveMarks().catch(() => {});
}

// ── Closed trades table ──────────────────────────────────────────────────────
function renderClosedTrades() {
  const wrap = document.getElementById('engClosedWrap');
  if (!wrap) return;

  const closed = closedTrades();
  const noteEl = document.getElementById('engClosedNote');
  if (noteEl) noteEl.textContent = closed.length ? `${closed.length} round-trips · scrollable ledger` : '0 round-trips · forward ledger active';

  if (!closed.length) {
    wrap.innerHTML = `<div style="text-align: center; padding: 36px 20px; color: var(--text3); font-size: 13.5px; font-style: italic;">
      No closed forward trades yet for ${BOOKS[_book].label}.<br/>
      <span style="font-size: 12px; color: var(--text3); display: block; margin-top: 6px; font-style: normal;">Forward paper trading began at ${fmtMoney(BOOK_START_EQUITY)} USD. Trades executed by the engine will appear here as they complete.</span>
    </div>`;
    return;
  }

  const rows = closed.map(t => {
    const inst = String(t.instrument || '');
    const cls = paperClassFor(inst);
    const isLong = String(t.direction || '').toLowerCase() !== 'short';
    const pnl = num(t.pnl);
    const retPct = num(t.return_pct);
    const defaultReason = (t.win || (pnl !== null && pnl > 0))
      ? (t.pyramided ? 'PYRAMID (+1.5R)' : 'WIN / TRAIL')
      : 'STOP LOSS';
    const reason = t.exit_reason ? String(t.exit_reason).toUpperCase() : defaultReason;
    return `<tr class="wl-row">
      <td style="color: var(--text3); font-size: 11px; white-space: nowrap;">${escHtml(t.exit_time ? fmtDay(t.exit_time) : '—')}</td>
      <td><strong style="font-family: var(--mono); font-size: 12px; font-weight: 700; color: var(--text);">${escHtml(inst)}</strong></td>
      <td>${dirBadgeCompact(isLong)}</td>
      <td style="font-family: var(--mono); font-size: 11px; color: var(--text2);">${escHtml(fmtQty(t.units))}</td>
      <td style="font-family: var(--mono); font-size: 11px; color: var(--text2);">${escHtml(fmtPrice(t.entry_price, cls))}</td>
      <td style="font-family: var(--mono); font-size: 11px; color: var(--text); font-weight: 600;">${escHtml(fmtPrice(t.exit_price, cls))}</td>
      <td>${pnlBadgeCompact(pnl, retPct)}</td>
      <td><span style="font-size:9.5px;font-weight:600;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,0.04);color:var(--text2);font-family:var(--mono);border:1px solid var(--border);">${escHtml(reason)}</span></td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<div class="wl-table-wrap eng-closed-table-wrap"><table class="wl-table eng-compact-table">
    <thead><tr>
      <th>Exit Date</th><th>Instrument</th><th>Direction</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Realized P&amp;L</th><th>Exit Reason</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

// ── Render all ───────────────────────────────────────────────────────────────
function renderAll() {
  renderHero();
  renderBookStats();
  renderProgressTracker();
  renderEquityChart();
  renderPositions();
  renderClosedTrades();
}

// ── Live Intraday Marks (Overlay) ────────────────────────────────────────────
const _liveMarks = {};
const LIVE_MARK_TTL = 45000;
let _liveTimer = null;

function normalizeCandleSymbol(sym, cls) {
  return sym;
}

async function fetchLiveMark(inst, cls) {
  const to = Math.floor(Date.now() / 1000);
  const from = to - 7 * 86400;
  const type = cls === 'forex' ? 'Forex' : (cls === 'crypto' ? 'Crypto' : 'Stock');
  const sym = normalizeCandleSymbol(inst, cls);
  const res = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const bars = await res.json();
  if (!Array.isArray(bars) || !bars.length) return null;
  const px = num(bars[bars.length - 1].close);
  if (px === null) return null;
  const prevClose = bars.length > 1 ? num(bars[bars.length - 2].close) : null;
  return { px, prevClose };
}

async function refreshLiveMarks() {
  if (document.hidden) return;
  const wrap = document.getElementById('engPositionsWrap');
  if (!wrap) return;
  const cards = wrap.querySelectorAll('.eng-pos-card');
  if (!cards.length) return;

  const stale = _book === 'r' ? [] : [{ inst: 'GBP/USD', cls: 'forex' }];
  for (const card of cards) {
    const inst = card.dataset.instrument;
    if (!inst) continue;
    if (!_liveMarks[inst] || (Date.now() - _liveMarks[inst].at) > LIVE_MARK_TTL) {
      stale.push({ inst, cls: paperClassFor(inst) });
    }
  }

  await Promise.allSettled(stale.map(async ({ inst, cls }) => {
    try {
      const mark = await fetchLiveMark(inst, cls);
      if (mark) {
        _liveMarks[inst] = { px: mark.px, prevClose: mark.prevClose, at: Date.now() };
        if (inst === 'GBP/USD') _gbpUsdRate = mark.px;
      }
    } catch (e) { /* keep official mark on failure */ }
  }));

  applyLiveMarks();
  setLastSyncLabel(new Date());
}

function applyLiveMarks() {
  const wrap = document.getElementById('engPositionsWrap');
  if (!wrap) return;

  let liveTotalOpenPnl = 0;
  let officialMarkedOpenPnl = 0;
  let liveTotalGross = 0;
  let liveCount = 0;

  // Calculate live PnL across all open positions in the book
  for (const p of _positions) {
    const inst = String(p.instrument || '');
    const m = _liveMarks[inst];
    if (!m) continue;
    const entry = num(p.entry_price);
    const units = num(p.units);
    if (entry === null || units === null) continue;
    const isLong = String(p.direction || '').toLowerCase() !== 'short';
    const livePnl = calcTradePnl(inst, entry, m.px, units, isLong, _gbpUsdRate);
    const officialPnl = calcTradePnl(inst, entry, num(p.last_px), units, isLong, _gbpUsdRate);
    if (livePnl !== null) {
      liveTotalOpenPnl += livePnl;
      if (officialPnl !== null) officialMarkedOpenPnl += officialPnl;
      liveTotalGross += (_book === 'r' || _book === 'f') ? (m.px * units) : (m.px * units) / (_gbpUsdRate || 1.285);
      liveCount++;
    }
  }

  // Update open cards in DOM
  for (const card of wrap.querySelectorAll('.eng-pos-card')) {
    const inst = card.dataset.instrument;
    const m = _liveMarks[inst];
    if (!m) continue;
    const entry = num(card.dataset.liveEntry);
    const units = num(card.dataset.liveUnits);
    if (entry === null || units === null) continue;
    const cls = paperClassFor(inst);
    const isLong = card.dataset.liveDir !== 'short';
    const livePnl = calcTradePnl(inst, entry, m.px, units, isLong, _gbpUsdRate);
    if (livePnl === null) continue;

    // 1. Directly update card Last Price
    const lastPxEl = card.querySelector('.card-last-px');
    if (lastPxEl) {
      lastPxEl.textContent = fmtPrice(m.px, cls);
    }

    // 2. Directly update card's main primary Unrealized P&L
    const upnlEl = card.querySelector('.card-upnl-val');
    if (upnlEl) {
      upnlEl.className = 'card-upnl-val ' + (livePnl > 0 ? 'pos' : (livePnl < 0 ? 'neg' : ''));
      upnlEl.textContent = fmtSignedMoney(livePnl);
    }

    // 3. Update Live Intraday row with live confirmation
    const liveRow = card.querySelector('.live-mark-val');
    if (liveRow) {
      liveRow.innerHTML = `<span style="color:var(--green);font-weight:600;">● Active</span> · ${escHtml(fmtPrice(m.px, cls))}`;
    }
  }

  // 4. Directly replace the main OPEN P&L summary stat with the live total
  if (liveCount > 0) {
    setText('engUnreal', fmtSignedMoney(liveTotalOpenPnl), pnlClass(liveTotalOpenPnl));
    const unrealSub = document.getElementById('engUnrealSub');
    if (unrealSub) unrealSub.textContent = 'live real-time aggregate';

    if (liveTotalGross > 0) {
      setText('engGross', fmtMoney(liveTotalGross));
    }

    // 5. Update hero equity and net return with live floating P&L
    const closed = closedTrades();
    const realizedBanked = closed.reduce((s, t) => s + (num(t.pnl) || 0), 0);
    const latest = latestDaily();
    const officialEquity = latest ? num(latest.equity) : BOOK_START_EQUITY;
    const liveTotalEquity = (_book === 'r' || _book === 'f')
      ? officialEquity + (liveTotalOpenPnl - officialMarkedOpenPnl)
      : BOOK_START_EQUITY + realizedBanked + liveTotalOpenPnl;
    const liveTotalNetReturn = liveTotalEquity - BOOK_START_EQUITY;

    setText('engEquity', fmtMoney(liveTotalEquity));
    setText('engCumPnl', fmtSignedMoney(liveTotalNetReturn), pnlClass(liveTotalNetReturn));

    const sinceChip = document.getElementById('engSinceChip');
    if (sinceChip) {
      const pct = (liveTotalNetReturn / BOOK_START_EQUITY) * 100;
      sinceChip.textContent = `Net Return: ${fmtSignedMoney(liveTotalNetReturn)} (${liveTotalNetReturn >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
      sinceChip.style.color = liveTotalNetReturn >= 0 ? 'var(--green)' : 'var(--red)';
    }

    const dayChip = document.getElementById('engDayChip');
    if (dayChip) {
      const priorEquity = latest ? num(latest.equity) : BOOK_START_EQUITY;
      const liveDayPnl = liveTotalEquity - priorEquity;
      dayChip.textContent = `Today: ${fmtSignedMoney(liveDayPnl)}`;
      dayChip.style.color = liveDayPnl >= 0 ? 'var(--green)' : 'var(--red)';
      setText('engDayPnl', fmtSignedMoney(liveDayPnl), pnlClass(liveDayPnl));
    }

    // 6. Update current drawdown live
    let peakEquity = BOOK_START_EQUITY;
    for (const r of _dailyRows) {
      const eq = num(r.equity);
      if (eq !== null && eq > peakEquity) peakEquity = eq;
    }
    if (liveTotalEquity > peakEquity) peakEquity = liveTotalEquity;
    const liveCurDD = peakEquity > 0 ? Math.max(0, (peakEquity - liveTotalEquity) / peakEquity) : 0;
    setText('engCurDD', liveCurDD > 0 ? '-' + (liveCurDD * 100).toFixed(2) + '%' : '0.00%', liveCurDD > 0 ? 'red' : '');

    _latestLiveEquity = liveTotalEquity;
    renderProgressTracker(liveTotalOpenPnl, officialMarkedOpenPnl);

    // 7. Update Equity Curve chart series with live point
    if (_eqSeries && _eqChart) {
      const todayStr = new Date().toISOString().slice(0, 10);
      const pts = [];
      const seen = new Set();
      for (const r of _dailyRows) {
        const eq = num(r.equity);
        if (!r.date || eq === null || seen.has(r.date)) continue;
        seen.add(r.date);
        pts.push({ time: r.date, value: eq });
      }
      if (pts.length) {
        if (pts[pts.length - 1].time === todayStr) {
          pts[pts.length - 1].value = liveTotalEquity;
        } else {
          pts.push({ time: todayStr, value: liveTotalEquity });
        }
        const isUp = liveTotalEquity >= (pts[0] ? pts[0].value : BOOK_START_EQUITY);
        _eqSeries.applyOptions({
          lineColor: isUp ? '#34D399' : '#F87171',
          topColor: isUp ? 'rgba(52, 211, 153, 0.22)' : 'rgba(248, 113, 113, 0.22)',
        });
        _eqSeries.setData(pts);
        _eqChart.timeScale().fitContent();
      }
    }

    const heroLine = document.getElementById('engHeroLine');
    if (heroLine) {
      heroLine.innerHTML = `<span style="color:var(--green);">● Live market marks active</span> · updated ${new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })} UK`;
    }
  }
}

// ── Book toggle (A = frozen proof / B = challenger) ──────────────────────────
function applyBookChrome() {
  const meta = BOOKS[_book];
  for (const btn of document.querySelectorAll('.vt-btn[data-book]')) {
    btn.classList.toggle('active', btn.dataset.book === _book);
  }
  const blurb = document.getElementById('engBookBlurb');
  if (blurb) blurb.textContent = meta.blurb;
  const since = document.getElementById('engSinceSub');
  if (since) since.textContent = 'since ' + meta.startLabel;
  const experiment = document.getElementById('engExperimentLabel');
  if (experiment) experiment.textContent = (_book === 'r' || _book === 'f')
    ? 'Forward Paper · $100k USD Account'
    : 'Paper Proof · £100k Experiment';
  const heroLabel = document.getElementById('engHeroLabel');
  if (heroLabel) heroLabel.textContent = _book === 'f'
    ? 'Book F (Prop Shield Elite) — Forward Paper'
    : (_book === 'r' ? 'Book R-252 — Forward Paper' : 'Engine Proof Book — Paper');
}

function setBook(book) {
  if (!BOOKS[book] || book === _book) return;
  _book = book;
  const url = new URL(window.location.href);
  url.searchParams.set('book', book);
  window.history.replaceState(null, '', url);
  _dailyRows = [];
  _positions = [];
  applyBookChrome();
  loadEngineBook().catch(e => console.error('Book switch load err:', e));
}

function initBookToggle() {
  for (const btn of document.querySelectorAll('.vt-btn[data-book]')) {
    btn.addEventListener('click', () => setBook(btn.dataset.book));
  }
  initClassToggle();
  applyBookChrome();
}

// ── Refresh / polling / realtime ─────────────────────────────────────────────
function initRefreshButton() {
  const btnRefresh = document.getElementById('btnRefresh');
  if (!btnRefresh) return;
  let rotation = 0;
  btnRefresh.addEventListener('click', async () => {
    const icon = document.getElementById('refreshIcon');
    const text = document.getElementById('refreshText');
    rotation += 360;
    if (icon) icon.style.transform = `rotate(${rotation}deg)`;
    if (text) text.textContent = 'Syncing...';

    btnRefresh.disabled = true;
    btnRefresh.style.opacity = '0.7';

    try {
      await loadEngineBook();
      await refreshLiveMarks();
      setLastSyncLabel(new Date());
    } catch (e) {
      console.error('Refresh fetch error:', e);
    } finally {
      setTimeout(() => {
        btnRefresh.disabled = false;
        btnRefresh.style.opacity = '1';
        if (text) text.textContent = 'Refresh Book';
      }, 600);
    }
  });
}

let _pollIntervalId = null;
function startPolling(ms) {
  if (_pollIntervalId) clearInterval(_pollIntervalId);
  _pollIntervalId = setInterval(() => {
    try { loadEngineBook(); } catch (e) { console.error('Poll refresh error:', e); }
  }, ms);
}

// ── Supabase Realtime: push updates, no refresh ──────────────────────────────
const SUPA_RT_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_RT_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
const RT_TABLES = [
  'apex_paper_positions', 'apex_paper_daily',
  'apex_paper_b_positions', 'apex_paper_b_daily',
  'apex_paper_c_positions', 'apex_paper_c_daily',
  'apex_analyses'
];
let _rtDebounce = null;

function setLivePill(connected) {
  const pill = document.querySelector('.live-pill');
  if (!pill) return;
  pill.style.opacity = connected ? '1' : '0.45';
  pill.title = connected
    ? 'Realtime connected — changes push to this page instantly'
    : 'Realtime disconnected — 15-minute fallback polling active';
}

function initRealtime() {
  if (!window.supabase || !window.supabase.createClient) {
    setLivePill(false);
    return;
  }
  const client = window.supabase.createClient(SUPA_RT_URL, SUPA_RT_ANON);
  const trigger = () => {
    if (_rtDebounce) clearTimeout(_rtDebounce);
    _rtDebounce = setTimeout(() => {
      if (document.hidden) return;
      try { loadEngineBook(); } catch (e) { console.error('Realtime reload err:', e); }
    }, 5000);
  };
  const channel = client.channel('engine-book-live');
  for (const t of RT_TABLES) {
    channel.on('postgres_changes', { event: '*', schema: 'public', table: t }, trigger);
  }
  channel.subscribe((status) => {
    setLivePill(status === 'SUBSCRIBED');
    if (status === 'SUBSCRIBED') console.log('Realtime live — push updates active');
  });
}

function bootEngineBook() {
  try { initBookToggle(); } catch (e) { console.error('Book toggle err:', e); }
  try { initRefreshButton(); } catch (e) { console.error('Refresh btn err:', e); }
  try { initRealtime(); } catch (e) { console.error('Realtime err:', e); }

  // Initial load + slow 15-minute background fallback (Realtime is primary)
  try { loadEngineBook(); } catch (e) { console.error('Initial load err:', e); }
  startPolling(900000);

  // Live intraday price polling (every 60s while active tab)
  if (!_liveTimer) _liveTimer = setInterval(refreshLiveMarks, 60000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshLiveMarks();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootEngineBook);
} else {
  bootEngineBook();
}
