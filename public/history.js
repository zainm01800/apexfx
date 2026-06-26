// ── APEX History Page ────────────────────────────────────────────────────────
// Fetches all saved scans from /api/memory and presents them GROUPED BY SYMBOL:
// one card per instrument showing the CURRENT call + an expandable thesis-evolution
// trail of prior scans, the symbol's realised win/loss record, and an anti-anchoring
// flag when the same direction has been re-scanned repeatedly without resolving.
// Also resolves pending outcomes against fresh candle data.

const API_MEMORY = '/api/memory';
const API_CANDLES = '/api/candles';   // same endpoint dashboard uses

// ── State ────────────────────────────────────────────────────────────────────
let _allRows      = [];   // all rows from Supabase (flat)
let _rowById      = {};   // id → row, for the Preview modal + Update lookups
let _valReliability = {}; // learned: how re-check assessments predict outcomes
let _livePx       = {};   // sym → latest price, for the "distance to entry" indicator
let _filterOutcome = 'all';
let _filterType    = 'all';
let _filterSym     = '';

function indexRows() { _rowById = {}; for (const r of _allRows) _rowById[r.id] = r; }

// A row is "auto" when the nightly auto-scan workflow generated it (tagged in
// setup_features.auto). Used to keep the user's personal track record separate from
// the bot's data-accumulation scans.
function isAuto(row) {
  let f = row && row.setup_features;
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
  return !!(f && f.auto);
}

// Trade style the scan was run for (Scalp/Intraday/Swing/Position), from setup_features.
const _STYLE_TF = { scalp: '15m chart', intraday: '1h chart', swing: 'daily chart', position: 'weekly chart' };
function tradeStyleOf(row) {
  let f = row && row.setup_features;
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
  if (!f || !f.style) return null;
  const s = String(f.style);
  return { label: s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(), tf: _STYLE_TF[s.toLowerCase()] || '' };
}

// key_reasons is persisted as a JSON string; tolerate array / string / null.
function parseReasons(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string' && v.trim()) {
    try { const a = JSON.parse(v); return Array.isArray(a) ? a : [v]; } catch { return [v]; }
  }
  return [];
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function fetchAllScans() {
  // 500 recent rows ≈ 2-3 weeks of history at ~200 scans/week — enough that open
  // swing trades stay visible (and resolvable) instead of falling off the window.
  const res = await fetch(`${API_MEMORY}?all=true&limit=500`);
  if (!res.ok) throw new Error('Failed to load scan history');
  return res.json();
}

// Style-aware outcome resolution. Each trade style is graded on a matching-
// granularity timeframe (so intrabar TP/SL is detected accurately) with an expiry
// scaled to its holding horizon; the style is read from the row's persisted
// setup_features (rows without it default to swing). Direction comes from verdictDir
// so SHORT verdicts resolve correctly (they say "SHORT", not "sell").
const STYLE_RES = {
  scalp:    { tf: '15m', expiryDays: 3,   bufferDays: 1 },
  intraday: { tf: '1h',  expiryDays: 7,   bufferDays: 2 },
  swing:    { tf: '1d',  expiryDays: 30,  bufferDays: 5 },
  position: { tf: '1d',  expiryDays: 120, bufferDays: 7 },
};
// Bar length per timeframe, used to require a FULL bar-period of clearance after the
// entry before a bar may grade TP/SL (no-look-ahead — see resolveIfPending).
const TF_SECONDS = { '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800 };
// UTC calendar day ("2026-06-05") for the daily no-look-ahead gate.
function utcDay(ts) { return new Date(ts * 1000).toISOString().slice(0, 10); }
function resolutionFor(row) {
  let f = row && row.setup_features;
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
  const s = (f && f.style ? String(f.style) : 'swing').toLowerCase();
  return STYLE_RES[s] || STYLE_RES.swing;
}

// TP/SL grading for ONE row against its candles (no expiry). Returns 'tp_hit' |
// 'sl_hit' | null. Shared by the pending-resolution AND the phantom self-heal paths
// so the two can never diverge. Honours the no-look-ahead gate (daily = strictly later
// calendar day; intraday = one bar-period clearance) + the entry-fill gate (a TP/SL
// only counts once price has actually traded INTO the entry zone).
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

  // Sanitize opening print spikes for Stocks/ETFs (Yahoo Finance data anomalies).
  // The literal FIRST bar of each session (the opening-auction print) can be a
  // garbled cross where Yahoo's free feed reports a wildly distorted range — and
  // verified live (NFLX 2026-06-26 13:30 UTC: O:71.60 H:73.56 L:71.54 C:73.51) the
  // distortion can corrupt the OPEN/CLOSE too, not just an extreme wick — that bar
  // alone made a 72.20 SHORT target look "hit" while every other 15m bar that
  // session traded 73.2-75.2 and the real exchange tape never went near 72. A
  // wick-clip (bodyMin*0.996) doesn't help when the body itself is the bad print,
  // so EXCLUDE the opening bar entirely from grading (and from the entry-fill
  // check) rather than trying to repair its values — confirmed against the live
  // NFLX data to correctly recover the real sl_hit at 15:30 UTC.
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
      if (bar.low  <= tp) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'tp_hit'; }
      if (bar.high >= sl) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'sl_hit'; }
    } else {
      if (bar.high >= tp) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'tp_hit'; }
      if (bar.low  <= sl) { row.filled_at = filledAt; row._resolved_at = bar.time * 1000; return 'sl_hit'; }
    }
  }
  if (filled) row.filled_at = filledAt;
  return null;
}

async function resolveIfPending(rows) {
  // Resolve PENDING rows, and SELF-HEAL already-resolved tp/sl rows: revert any
  // "phantom" whose entry never filled / that no longer holds under the current rules
  // (a stale resolution the grader can't otherwise reach). Only reverts resolved →
  // pending/expired; NEVER flips tp↔sl (intrabar ambiguity), so it can't flip-flop.
  const relevant = rows.filter(r => r.target_price && r.stop_loss && r.price &&
    (r.outcome === 'pending' || r.outcome === 'tp_hit' || r.outcome === 'sl_hit'));
  if (!relevant.length) return;

  // Group by symbol + resolution timeframe, fetching the right-granularity candles once.
  const groups = {};
  for (const r of relevant) {
    const res = resolutionFor(r);
    const key = r.symbol + '|' + res.tf;
    (groups[key] ||= { sym: r.symbol, type: r.asset_type || 'Stock', tf: res.tf, rows: [] }).rows.push({ row: r, res });
  }

  await Promise.allSettled(
    Object.values(groups).map(async (g) => {
      try {
        const oldest = Math.min(...g.rows.map(x => rowTs(x.row) / 1000));
        const buffer = Math.max(...g.rows.map(x => x.res.bufferDays)) * 86400;
        const from   = Math.floor(oldest - buffer);
        const to     = Math.floor(Date.now() / 1000);
        const candleRes = await fetch(
          `${API_CANDLES}?sym=${encodeURIComponent(g.sym)}&type=${encodeURIComponent(g.type)}&tf=${g.tf}&from=${from}&to=${to}`
        );
        if (!candleRes.ok) return;
        const candles = await candleRes.json();
        if (!Array.isArray(candles) || candles.length < 2) return;

        for (const { row, res } of g.rows) {
          const graded  = gradeRow(row, res, candles);
          const ageDays = (Date.now() / 1000 - rowTs(row) / 1000) / 86400;

          if (row.outcome === 'pending') {
            const resolved = graded || (ageDays > res.expiryDays ? 'expired' : null);
            if (resolved) {
              row.outcome = resolved;
              const resolvedTime = row._resolved_at ? new Date(row._resolved_at).toISOString() : new Date().toISOString();
              row.outcome_date = resolvedTime;
              fetch(API_MEMORY, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: row.id, outcome: resolved, outcome_date: resolvedTime }),
              }).catch(() => {});
            }
          } else if (graded === null) {
            // PHANTOM self-heal: stored tp/sl that current data no longer supports
            // (e.g. the entry never filled). Revert — expired if old enough (the setup
            // never triggered), else pending so it can resolve correctly later.
            const reverted = ageDays > res.expiryDays ? 'expired' : 'pending';
            row.outcome = reverted;
            const outcomeDate = reverted === 'expired' ? new Date().toISOString() : null;
            row.outcome_date = outcomeDate;
            fetch(API_MEMORY, {
              method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: row.id, outcome: reverted, outcome_price: null,
                outcome_date: outcomeDate, lesson: '' }),
            }).catch(() => {});
          }
          // graded matches the stored outcome → leave it; differs (tp↔sl) → leave it
          // (don't flip-flop on intrabar ambiguity).
        }
      } catch {}
    })
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function verdictClass(v) {
  if (!v) return '';
  const lv = v.toLowerCase().replace(/_/g, '-');
  if (lv.includes('strong-buy') || lv.includes('strong buy')) return 'strong-buy';
  if (lv.includes('buy'))  return 'buy';
  if (lv.includes('sell')) return lv.includes('strong') ? 'strong-sell' : 'sell';
  return 'hold';
}

// Bucket a verdict into a trade direction (for anchoring detection).
function verdictDir(v) {
  const u = (v || '').toUpperCase();
  if (/BUY/.test(u)) return 'long';
  if (/SELL|SHORT/.test(u)) return 'short';
  return 'neutral';
}

function outcomeLabel(o) {
  switch (o) {
    case 'tp_hit':  return '✅ TP Hit';
    case 'sl_hit':  return '❌ SL Hit';
    case 'expired': return '⏱ Expired';
    default:        return '⏳ Pending';
  }
}

function fmtPrice(p) {
  if (p == null || p === '') return '—';
  const n = parseFloat(p);
  if (isNaN(n)) return p;
  return n < 10 ? n.toFixed(5) : n < 1000 ? n.toFixed(2) : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// Best-effort timestamp for a row: created_at → epoch baked into the id → analysis_date.
function rowTs(row) {
  if (row.created_at) { const t = Date.parse(row.created_at); if (!isNaN(t)) return t; }
  const m = String(row.id || '').match(/_(\d{10,})$/);
  if (m) return parseInt(m[1], 10);
  if (row.analysis_date) { const t = Date.parse(row.analysis_date); if (!isNaN(t)) return t; }
  return 0;
}

// Scan time of day in UTC ("17:30 UTC") — shown alongside the date so trades scanned
// on the same day (or across a midnight boundary) are never ambiguous. UTC matches the
// candle data the outcomes are graded against. Empty when there's no usable timestamp.
function fmtTimeUTC(row) {
  const t = rowTs(row);
  if (!t) return '';
  // Skip midnight-only fallbacks (analysis_date with no real time component).
  if (!row.created_at && !/_(\d{10,})$/.test(String(row.id || ''))) return '';
  const d = new Date(t);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
}
// Date + time, e.g. "2026-06-05 · 17:30 UTC".
function fmtDateTime(row) {
  const time = fmtTimeUTC(row);
  return `${row.analysis_date || ''}${time ? ' · ' + time : ''}`;
}

// Format outcome date/time, e.g. "2026-06-05 · 17:30 UTC" or fallback to "2026-06-05" if no time exists.
function fmtOutcomeDateTime(outcomeDate) {
  if (!outcomeDate) return '';
  if (outcomeDate.includes('T') || outcomeDate.includes(' ')) {
    const d = new Date(outcomeDate);
    if (!isNaN(d.getTime())) {
      const datePart = d.toISOString().slice(0, 10);
      const timePart = `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
      return `${datePart} · ${timePart}`;
    }
  }
  return outcomeDate;
}

// "Update" re-runs the FULL analysis to RE-CHECK an existing trade's validity. It does
// NOT create a new trade — the dashboard attaches a validation record (still valid /
// weakening / invalidated + price progress) to the original via `validate=ID`.
function updateUrl(row) {
  // Carry the trade's OWN style so the re-check runs on the same timeframe (an intraday
  // trade is re-checked as intraday, not the dashboard's default swing).
  const st = tradeStyleOf(row);
  const styleQ = st ? `&style=${encodeURIComponent(st.label.toLowerCase())}` : '';
  return `dashboard.html?sym=${encodeURIComponent(row.symbol)}&validate=${encodeURIComponent(row.id)}${styleQ}`;
}

// Parse the validations array (JSONB → array / string / null) and return the latest.
function parseValidations(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string' && v.trim()) { try { const a = JSON.parse(v); return Array.isArray(a) ? a : []; } catch { return []; } }
  return [];
}
function fmtAway(x) { return (x < 1 ? x.toFixed(2) : x.toFixed(1)) + '%'; }

// How far the current price is from a trade's entry zone (0 = inside it), and whether
// price has moved toward or away from entry SINCE THE ORIGINAL SCAN (the stable, intuitive
// "since the call was made" reference — not the last re-check, which was confusing).
function entryProximity(row, px) {
  const b = entryBounds(row.entry_zone);
  if (!b || px == null || isNaN(px)) return null;
  const distOf = p => (p >= b.lo && p <= b.hi) ? 0 : Math.abs(p - (p < b.lo ? b.lo : b.hi)) / Math.abs(p) * 100;
  const now = distOf(px);
  const scanPx = parseFloat(row.price);
  const scanDist = !isNaN(scanPx) ? distOf(scanPx) : null;
  let dir = null;   // vs the original scan price
  if (scanDist != null) {
    const delta = now - scanDist;
    dir = Math.abs(delta) < 0.1 ? 'flat' : delta < 0 ? 'closer' : 'further';
  }
  return { pct: now, inZone: now === 0, dir };
}

// How the card should treat a verdict:
//  'trade' = a real directional setup → entry/stop/target are a LIVE plan.
//  'watch' = WAIT / NO_EDGE / HOLD → NOT a trade; the levels are conditional ("the
//            level it would need to become valid"), so status is WATCHING, never LIVE.
//  'avoid' = AVOID / REDUCE / HEDGE → actively no trade.
function verdictKind(v) {
  const u = (v || '').toUpperCase();
  if (/BUY|SELL|SHORT|LONG/.test(u)) return 'trade';
  if (/AVOID|REDUCE|HEDGE/.test(u))  return 'avoid';
  return 'watch';   // WAIT, NO_EDGE, HOLD, or unknown
}

const _STATUS = {
  live:        { txt: '🟢 LIVE',        tip: 'Price is in the entry zone now — this trade can be entered.' },
  approaching: { txt: '🔵 APPROACHING', tip: 'Not entered yet, but price has moved toward the entry since the scan.' },
  drifting:    { txt: '🟠 DRIFTING',    tip: 'Not entered — price has moved AWAY from the entry since the scan.' },
  waiting:     { txt: '⏳ WAITING',     tip: 'Not entered — price is sitting away from the entry zone.' },
};
// Plain-English status of an OPEN scan vs the live price — verdict-aware so a WAIT is
// never shown as "LIVE". Answers "is this an actual trade right now, or not?".
function tradeStatus(row, px) {
  if (!(row.outcome == null || row.outcome === 'pending')) return null;   // resolved → outcome shown elsewhere
  const kind = verdictKind(row.verdict);
  if (kind === 'avoid') return { cls: 'avoid', label: '⛔ NO TRADE', sub: '', tip: 'The verdict is to avoid / reduce — not a setup to enter.' };
  const p = entryProximity(row, px);
  if (kind === 'watch') {
    if (!p)         return { cls: 'watch',     label: '👀 WATCHING',       sub: '', tip: 'No trade yet — waiting for the setup to become valid. The levels are conditional, not a live entry.' };
    if (p.inZone)   return { cls: 'watch-hit', label: '⚡ LEVEL REACHED',   sub: 're-check', tip: 'Price has reached the watch level — hit Update to re-check whether it is now an actual trade.' };
    return            { cls: 'watch',          label: '👀 WATCHING',       sub: fmtAway(p.pct) + ' to level', tip: 'No trade yet — the levels are conditional, not a live entry.' };
  }
  // kind === 'trade'
  if (!p) return null;
  const cls = p.inZone ? 'live' : p.dir === 'closer' ? 'approaching' : p.dir === 'further' ? 'drifting' : 'waiting';
  const s = _STATUS[cls];
  return { cls, label: s.txt, tip: s.tip, sub: p.inZone ? '' : fmtAway(p.pct) + ' away' };
}

const _VAL_LABEL = { confirmed: 'STILL VALID', weakening: 'WEAKENING', invalidated: 'INVALIDATED', activated: 'NOW ACTIONABLE', 'still-waiting': 'STILL WAITING', 'n/a': 'RE-CHECKED' };
function validationSummary(v) {
  const label = _VAL_LABEL[v.assessment] || 'RE-CHECKED';
  const conf = v.confidence != null ? ` ${v.confidence}%` : '';
  const then = v.confidenceThen != null && v.confidence != null && v.confidenceThen !== v.confidence ? ` (was ${v.confidenceThen}%)` : '';
  const prog = v.progressPct != null ? ` · ${v.progressPct}% to ${v.progressToward}` : '';
  return `${label} · ${(v.verdict || '').replace(/_/g, ' ')}${conf}${then}${prog}`;
}
// "Jun 5, 17:30 UTC" from a validation record's ISO timestamp.
function fmtUTC(d) {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} · ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC`;
}
function fmtValTs(ts) {
  const t = Date.parse(ts); if (isNaN(t)) return '';
  return fmtUTC(new Date(t));
}
// filled_at is UNIX SECONDS (set by gradeRow during resolution), unlike the ISO
// strings used elsewhere — needs its own formatter.
function fmtUnixUTC(tsSec) {
  if (!tsSec) return '';
  return fmtUTC(new Date(tsSec * 1000));
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Grouping ───────────────────────────────────────────────────────────────────
// Collapse the flat scan list into one entry per symbol: the latest scan is the
// "current" call; the rest form the evolution trail. Also computes the realised
// win/loss record and an anti-anchoring flag for the symbol.
function buildGroups(rows) {
  // Symbol-level realised record — shown on each of that symbol's trade cards so you
  // still see the pair's overall hit-rate without folding distinct trades together.
  const bySym = {};
  for (const r of rows) (bySym[r.symbol] ||= []).push(r);
  const recOf = {};
  for (const [sym, scans] of Object.entries(bySym)) {
    const resolved = scans.filter(s => s.outcome === 'tp_hit' || s.outcome === 'sl_hit');
    const wins = resolved.filter(s => s.outcome === 'tp_hit').length;
    recOf[sym] = { resolved: resolved.length, wins, losses: resolved.length - wins,
      winRate: resolved.length ? Math.round((wins / resolved.length) * 100) : null };
  }

  // ONE card per TRADE. Same-direction re-scans already REFRESH a single DB row, so a
  // symbol only has multiple rows when they are genuinely DIFFERENT trades (a closed
  // one, a new direction, a post-resolution re-scan). Each gets its own card; a trade's
  // own evolution lives in its re-check history (validations), not in other trades.
  const cards = rows.map(row => {
    const rec = recOf[row.symbol];
    return {
      symbol: row.symbol, current: row, scans: [row], trail: [],
      resolved: rec.resolved, wins: rec.wins, losses: rec.losses, winRate: rec.winRate,
      anchorFlag: null, lesson: row.lesson || null, ts: rowTs(row),
    };
  });
  cards.sort((a, b) => b.ts - a.ts);   // newest trade first
  return cards;
}

// ── Trade lifeline ───────────────────────────────────────────────────────────
// The full chronological story of ONE trade, every step stamped with its own date
// + time so "when did this actually happen" is never implicit: Scanned -> Entered
// (when price actually traded into the entry zone — distinct from the scan time for
// a pullback/breakout entry) -> each re-check, in order -> Ended. Always visible on
// the card (not hidden behind a details toggle) — this IS the requested feature.
function buildLifeline(row) {
  const steps = [{ icon: '🔍', label: 'Scanned', time: fmtDateTime(row), cls: 'scan' }];

  const kind = verdictKind(row.verdict);
  const isOpen = (row.outcome == null || row.outcome === 'pending');
  if (kind === 'trade') {
    if (row.filled_at) {
      // Distinguish an immediate market fill from a pullback/breakout entry that
      // took real time to trade into — both are useful, different facts.
      const sameAsScan = Math.abs(row.filled_at - rowTs(row) / 1000) < 60;
      steps.push({
        icon: '✅', label: sameAsScan ? 'Entered (at market)' : 'Entered',
        time: fmtUnixUTC(row.filled_at), cls: 'enter',
      });
    } else if (isOpen) {
      steps.push({ icon: '⏳', label: 'Not entered yet', time: '', cls: 'wait' });
    }
  }

  for (const v of parseValidations(row.validations)) {
    steps.push({
      icon: '🔁', label: _VAL_LABEL[v.assessment] || 'Re-checked',
      time: fmtValTs(v.ts), sub: validationSummary(v) + reliabilityNote(v.assessment),
      cls: `recheck ${v.assessment || ''}`,
    });
  }

  if (!isOpen) {
    const endCls = row.outcome === 'tp_hit' ? 'win' : row.outcome === 'sl_hit' ? 'loss' : 'neutral';
    steps.push({
      icon: row.outcome === 'tp_hit' ? '🎯' : row.outcome === 'sl_hit' ? '🛑' : '⏱',
      label: outcomeLabel(row.outcome).replace(/^\S+\s/, ''),   // drop the emoji baked into outcomeLabel
      time: row.outcome_date ? fmtOutcomeDateTime(row.outcome_date) : '',
      cls: endCls,
    });
  }

  return `<div class="sc-lifeline">${steps.map(s => `
    <div class="ll-step ll-${s.cls}">
      <span class="ll-icon">${s.icon}</span>
      <div class="ll-body">
        <div class="ll-row"><span class="ll-label">${escHtml(s.label)}</span>${s.time ? `<span class="ll-time">${escHtml(s.time)}</span>` : ''}</div>
        ${s.sub ? `<div class="ll-sub">${escHtml(s.sub)}</div>` : ''}
      </div>
    </div>`).join('<div class="ll-connector"></div>')}</div>`;
}

// ── Rendering ───────────────────────────────────────────────────────────────────

function renderTrailRow(scan, prevOlder) {
  const vc = verdictClass(scan.verdict);
  const vDisplay = (scan.verdict || '—').replace(/_/g, ' ').toUpperCase();
  // confidence delta vs the next-older scan (chronologically before this one)
  let delta = '';
  if (prevOlder && scan.confidence != null && prevOlder.confidence != null) {
    const d = scan.confidence - prevOlder.confidence;
    if (d > 0)      delta = `<span class="tr-delta up">▲${d}</span>`;
    else if (d < 0) delta = `<span class="tr-delta dn">▼${Math.abs(d)}</span>`;
    else            delta = `<span class="tr-delta flat">·</span>`;
  }
  return `
    <div class="trail-row">
      <span class="tr-date">${escHtml(fmtDateTime(scan))}</span>
      <span class="tr-verdict ${vc}">${escHtml(vDisplay)}</span>
      <span class="tr-conf">${scan.confidence != null ? scan.confidence + '%' : '—'}${delta}</span>
      <span class="tr-px">@ ${fmtPrice(scan.price)}</span>
      <span class="tr-tgt">T ${scan.target_price ? fmtPrice(scan.target_price) : '—'} / S ${scan.stop_loss ? fmtPrice(scan.stop_loss) : '—'}</span>
      <span class="tr-outcome ${scan.outcome || 'pending'}">${outcomeLabel(scan.outcome)}</span>
    </div>`;
}

function renderCard(g) {
  const row = g.current;
  const vc  = verdictClass(row.verdict);
  const vDisplay = (row.verdict || '—').replace(/_/g, ' ').toUpperCase();

  // Realised-record badge for this symbol
  let recordBadge = '';
  if (g.resolved > 0) {
    const cls = g.winRate >= 50 ? 'rec-good' : 'rec-bad';
    recordBadge = `<span class="sc-record ${cls}" title="Realised outcomes of resolved calls on ${escHtml(g.symbol)}">📊 ${g.wins}W / ${g.losses}L · ${g.winRate}%</span>`;
  }

  const anchor = g.anchorFlag
    ? `<div class="sc-anchor" title="Repeated same-direction calls with no resolved outcome — beware anchoring">⚠ ${escHtml(g.anchorFlag)}</div>`
    : '';

  // The full chronological story — scanned, entered, every re-check, ended — each
  // stamped with its own date+time. Always visible (see buildLifeline).
  const lifeline = buildLifeline(row);

  // Post-mortem lesson — shown on the current call when it (or any resolved scan in
  // the group) has one. This is the "what went wrong / right" the engine learns from.
  const lessonRow = g.lesson
    ? `<div class="sc-lesson" title="AI post-mortem — fed back into future analysis of similar setups">📓 <strong>Lesson:</strong> ${escHtml(g.lesson)}</div>`
    : '';

  // Live status + "distance to entry" — verdict-aware. For a real trade: LIVE /
  // APPROACHING / DRIFTING / WAITING. For a WAIT/NO_EDGE: WATCHING / LEVEL-REACHED
  // (the levels are a conditional "watch" plan, NOT a live entry). For AVOID: NO TRADE.
  const _isOpen = (row.outcome == null || row.outcome === 'pending');
  const _kind = verdictKind(row.verdict);
  const _level = _kind === 'trade' ? 'entry zone' : 'watch level';
  const _status = _isOpen ? tradeStatus(row, _livePx[row.symbol]) : null;
  // When price has reached a WAIT's watch level, the badge IS the re-check action —
  // make it a real clickable link (same as the Update button), not dead text.
  const statusBadge = _status
    ? (_status.cls === 'watch-hit'
        ? `<a class="sc-status watch-hit clickable" href="${updateUrl(row)}" title="${escHtml(_status.tip)}">${_status.label}${_status.sub ? ` · ${_status.sub}` : ''}</a>`
        : `<span class="sc-status ${_status.cls}" title="${escHtml(_status.tip)}">${_status.label}${_status.sub ? ` · ${_status.sub}` : ''}</span>`)
    : '';
  const _prox = (_isOpen && _kind !== 'avoid') ? entryProximity(row, _livePx[row.symbol]) : null;
  const proxRow = _prox
    ? `<div class="sc-prox ${_prox.inZone ? 'inzone' : ''}" title="How far the live price is from this scan's ${_level} (vs the original scan)">🎯 ${_prox.inZone
        ? `Price is at the ${_level} now`
        : `${fmtAway(_prox.pct)} from the ${_level}${_prox.dir === 'further' ? ' — moved away since the scan' : _prox.dir === 'closer' ? ' — moved closer since the scan' : ''}`}</div>`
    : '';

  // For non-trade verdicts, make clear the entry/stop/target are conditional, not live.
  const condNote = (_isOpen && _kind !== 'trade')
    ? `<div class="sc-cond" title="This isn't an actionable trade right now">⏸ ${_kind === 'avoid'
        ? 'No trade — the verdict is to avoid/reduce; any levels below are context only.'
        : 'No trade yet — the entry/stop/target below are the levels it would need to <strong>become</strong> a valid trade, not a live position.'}</div>`
    : '';

  return `
    <div class="scan-card ${vc}">
      <div class="sc-head">
        <div>
          <div class="sc-sym">${escHtml(g.symbol)}</div>
          <div class="sc-date">Scanned ${escHtml(fmtDateTime(row))}</div>
        </div>
        <div class="sc-tags">
          <span class="sc-type">${escHtml(row.asset_type || 'Stock')}</span>
          ${(() => { const st = tradeStyleOf(row); return st ? `<span class="sc-style" title="Trade style this scan was run for — analysed on the ${escHtml(st.tf)}">${escHtml(st.label)}</span>` : ''; })()}
          ${isAuto(row) ? `<span class="sc-auto" title="Generated by the nightly auto-scan, not a call you ran">🤖 auto</span>` : ''}
        </div>
      </div>

      <div class="sc-verdict-row">
        <span class="sc-verdict ${vc}">${vDisplay}</span>
        <span class="sc-conf">${row.confidence != null ? row.confidence + '%' : '—'} confidence</span>
        ${statusBadge}
      </div>

      ${recordBadge ? `<div class="sc-record-row">${recordBadge}</div>` : ''}
      ${anchor}

      <div class="sc-price-row">
        <span class="sc-price">@ $${fmtPrice(row.price)}</span>
        <span class="sc-outcome ${row.outcome || 'pending'}">${outcomeLabel(row.outcome)}</span>
      </div>

      ${lifeline}

      ${row.summary ? `<p class="sc-summary">${escHtml(row.summary)}</p>` : ''}

      ${condNote}

      <div class="sc-targets${_isOpen && _kind !== 'trade' ? ' conditional' : ''}">
        ${row.entry_zone  ? `<div class="sc-target-item"><span class="sc-tl">${_kind === 'trade' ? 'Entry' : 'Watch'}</span><span class="sc-tv entry">${escHtml(row.entry_zone)}</span></div>`  : ''}
        ${row.target_price? `<div class="sc-target-item"><span class="sc-tl">${_kind === 'trade' ? 'Target' : 'If-target'}</span><span class="sc-tv target">$${fmtPrice(row.target_price)}</span></div>` : ''}
        ${row.stop_loss   ? `<div class="sc-target-item"><span class="sc-tl">${_kind === 'trade' ? 'Stop' : 'If-stop'}</span><span class="sc-tv stop">$${fmtPrice(row.stop_loss)}</span></div>`    : ''}
        ${row.risk_reward ? `<div class="sc-target-item"><span class="sc-tl">R:R</span><span class="sc-tv">${escHtml(row.risk_reward)}</span></div>`          : ''}
      </div>

      ${proxRow}

      ${lessonRow}

      <div class="sc-actions">
        ${_isOpen
          ? `<a class="sc-btn sc-btn-update" href="${updateUrl(row)}" title="Re-check this OPEN trade's validity — re-runs the analysis and refreshes this trade without creating a new one">🔄 Update</a>`
          : `<a class="sc-btn sc-btn-update" href="dashboard.html?sym=${encodeURIComponent(row.symbol)}" title="This trade is finished (${outcomeLabel(row.outcome)}) and frozen. Run a fresh scan to open a NEW, separate trade for ${escHtml(row.symbol)}">🔁 Scan again</a>`}
        <button class="sc-btn sc-btn-preview" data-action="preview" data-id="${escHtml(row.id)}" title="See the full analysis behind this call">
          👁 Preview
        </button>
      </div>
    </div>
  `;
}

// ── Filter + render ───────────────────────────────────────────────────────────

// A symbol group passes when: its symbol matches the search, its current type matches
// the type filter, and (for outcome) ANY scan in the group has that outcome — so
// filtering "TP Hit" surfaces every instrument that has ever hit a target.
function applyFilters(groups) {
  return groups.filter(g => {
    if (_filterSym && !g.symbol.toUpperCase().includes(_filterSym.toUpperCase())) return false;
    if (_filterType !== 'all' && g.current.asset_type !== _filterType) return false;
    if (_filterOutcome !== 'all' && !g.scans.some(s => (s.outcome || 'pending') === _filterOutcome)) return false;
    return true;
  });
}

function renderGrid() {
  const grid = document.getElementById('scanGrid');
  const empty = document.getElementById('histEmpty');
  _valReliability = computeValidationReliability(_allRows);   // refresh learned re-check stats
  const groups = applyFilters(buildGroups(_allRows));

  if (!groups.length) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  grid.innerHTML = groups.map(renderCard).join('');
}

// ── Preview modal ───────────────────────────────────────────────────────────
// Renders the FULL saved analysis (the same write-up the Research tab produced)
// in a popup — read-only, straight from the stored row, no re-scan needed.

function _section(title, body) {
  if (!body || !String(body).trim()) return '';
  return `<div class="pv-section"><h4>${escHtml(title)}</h4><p>${escHtml(body)}</p></div>`;
}

function openPreview(id) {
  const row = _rowById[id];
  if (!row) return;
  const modal = document.getElementById('previewModal');
  const body  = document.getElementById('pvBody');
  if (!modal || !body) return;

  const vc = verdictClass(row.verdict);
  const vDisplay = (row.verdict || '—').replace(/_/g, ' ').toUpperCase();
  const reasons = parseReasons(row.key_reasons);
  const outcomeCls = row.outcome || 'pending';

  const targets = [
    row.entry_zone   ? ['Entry',  escHtml(String(row.entry_zone)), 'entry']  : null,
    row.target_price ? ['Target', '$' + fmtPrice(row.target_price), 'target'] : null,
    row.stop_loss    ? ['Stop',   '$' + fmtPrice(row.stop_loss), 'stop']    : null,
    row.risk_reward  ? ['R:R',    escHtml(String(row.risk_reward)), '']      : null,
  ].filter(Boolean).map(([l, v, c]) =>
    `<div class="pv-target"><span class="pv-tl">${l}</span><span class="pv-tv ${c}">${v}</span></div>`).join('');

  body.innerHTML = `
    <div class="pv-head">
      <div>
        <div class="pv-sym">${escHtml(row.symbol)} <span class="pv-type">${escHtml(row.asset_type || 'Stock')}</span></div>
        <div class="pv-date">Analysed ${escHtml(fmtDateTime(row))} · @ $${fmtPrice(row.price)}</div>
      </div>
      <button class="pv-close" data-action="pv-close" aria-label="Close">✕</button>
    </div>

    <div class="pv-verdict-row">
      <span class="pv-verdict ${vc}">${vDisplay}</span>
      <span class="pv-conf">${row.confidence != null ? row.confidence + '%' : '—'} confidence</span>
      <span class="pv-outcome ${outcomeCls}">${outcomeLabel(row.outcome)}</span>
    </div>

    ${targets ? `<div class="pv-targets">${targets}</div>` : ''}

    <div class="pv-section"><h4>Timeline</h4>${buildLifeline(row)}</div>

    ${row.lesson ? `<div class="pv-lesson" title="AI post-mortem fed back into future analysis">📓 <strong>Lesson learned:</strong> ${escHtml(row.lesson)}</div>` : ''}

    ${row.summary ? `<div class="pv-section pv-summary"><h4>Executive summary</h4><p>${escHtml(row.summary)}</p></div>` : ''}

    ${reasons.length ? `<div class="pv-section"><h4>Key reasons</h4><ul class="pv-reasons">${reasons.map(r => `<li>${escHtml(String(r))}</li>`).join('')}</ul></div>` : ''}

    ${_section('Technical analysis',   row.technical_analysis)}
    ${_section('Fundamental analysis', row.fundamental_analysis)}
    ${_section('Macro environment',    row.macro_environment)}
    ${_section('Risk analysis',        row.risk_analysis)}
    ${_section('Short-term outlook',   row.short_term_outlook)}

    <div class="pv-foot">
      <a class="pv-btn" href="${updateUrl(row)}">🔄 Run an update on ${escHtml(row.symbol)}</a>
    </div>`;

  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closePreview() {
  const modal = document.getElementById('previewModal');
  if (modal) modal.style.display = 'none';
  document.body.style.overflow = '';
}

// One delegated listener handles Preview opens, the close button, and backdrop click.
function initGridActions() {
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-action]');
    if (t) {
      const action = t.dataset.action;
      if (action === 'preview')  { e.preventDefault(); openPreview(t.dataset.id); return; }
      if (action === 'pv-close') { e.preventDefault(); closePreview(); return; }
    }
    // Click on the modal backdrop (outside the panel) closes it.
    if (e.target.id === 'previewModal') closePreview();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePreview(); });
}

function updateSummary() {
  const total    = _allRows.length;
  const symbols  = new Set(_allRows.map(r => r.symbol)).size;
  const tp       = _allRows.filter(r => r.outcome === 'tp_hit').length;
  const sl       = _allRows.filter(r => r.outcome === 'sl_hit').length;
  const resolved = tp + sl;
  const accuracy = resolved > 0 ? Math.round(tp / resolved * 100) : null;

  setText('hsStat0', `${symbols}`,                              `Symbols · ${total} scans`);
  setText('hsStat1', tp,                                        'TP Hit',  'green');
  setText('hsStat2', sl,                                        'SL Hit',  'red');
  setText('hsStat3', accuracy != null ? accuracy + '%' : '—%',  'Accuracy', 'accent');
}

function setText(id, val, label, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.querySelector('.hs-val').textContent = val;
  el.querySelector('.hs-label').textContent = label;
  if (cls) el.querySelector('.hs-val').className = `hs-val ${cls}`;
}

// ── Filters wiring ────────────────────────────────────────────────────────────

function initFilters() {
  document.querySelectorAll('[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filterOutcome = btn.dataset.filter;
      renderGrid();
    });
  });

  document.querySelectorAll('[data-type]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-type]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filterType = btn.dataset.type;
      renderGrid();
    });
  });

  const searchEl = document.getElementById('hfSearch');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      _filterSym = searchEl.value.trim();
      renderGrid();
    });
  }
}

// ── Accuracy scoreboard ─────────────────────────────────────────────────────
// Aggregates realised outcomes across ALL scans into headline accuracy metrics.
function computeAccuracy(rows) {
  const total    = rows.length;
  const resolved = rows.filter(r => r.outcome === 'tp_hit' || r.outcome === 'sl_hit');
  const wins     = resolved.filter(r => r.outcome === 'tp_hit');
  const losses   = resolved.filter(r => r.outcome === 'sl_hit');
  const winRate  = resolved.length ? Math.round(wins.length / resolved.length * 100) : null;
  const pctResolved = total ? Math.round(resolved.length / total * 100) : 0;

  const avgConf = arr => arr.length ? Math.round(arr.reduce((s, r) => s + (Number(r.confidence) || 0), 0) / arr.length) : null;

  const dirOf = r => {
    const u = (r.verdict || '').toUpperCase();
    if (/BUY/.test(u)) return 'buy';
    if (/SELL|SHORT/.test(u)) return 'sell';
    return 'other';
  };
  const buyRes  = resolved.filter(r => dirOf(r) === 'buy');
  const sellRes = resolved.filter(r => dirOf(r) === 'sell');
  const acc = set => set.length ? Math.round(set.filter(r => r.outcome === 'tp_hit').length / set.length * 100) : null;

  const hiConf = resolved.filter(r => (Number(r.confidence) || 0) >= 80);

  // Brier score — mean squared error between the stated probability and the
  // realised binary outcome (1 = TP hit, 0 = SL hit). Lower is better; 0.25 is the
  // no-skill baseline (always saying 50%). This is the PROPER way to score
  // confidence: a win-rate / ranking can look fine while the probabilities
  // themselves are badly miscalibrated (overconfident).
  let brier = null;
  if (resolved.length) {
    const sum = resolved.reduce((s, r) => {
      const p = Math.min(1, Math.max(0, (Number(r.confidence) || 50) / 100));
      const o = r.outcome === 'tp_hit' ? 1 : 0;
      return s + (p - o) * (p - o);
    }, 0);
    brier = +(sum / resolved.length).toFixed(3);
  }

  // Reliability curve — realised hit-rate per stated-confidence band. A well-
  // calibrated model sits near the diagonal (80% band actually wins ~80%).
  const _bandMid = { '50–59': 55, '60–69': 65, '70–79': 75, '80–89': 85, '90+': 95 };
  const reliability = [
    { band: '50–59', lo: 0,  hi: 59 }, { band: '60–69', lo: 60, hi: 69 },
    { band: '70–79', lo: 70, hi: 79 }, { band: '80–89', lo: 80, hi: 89 },
    { band: '90+',   lo: 90, hi: 100 },
  ].map(b => {
    const set = resolved.filter(r => { const c = Number(r.confidence) || 0; return c >= b.lo && c <= b.hi; });
    const a   = set.length ? Math.round(set.filter(r => r.outcome === 'tp_hit').length / set.length * 100) : null;
    return { band: b.band, n: set.length, acc: a, gap: a == null ? null : a - _bandMid[b.band] };
  }).filter(b => b.n > 0);

  return {
    total, resolvedN: resolved.length, pctResolved, winRate,
    wins: wins.length, losses: losses.length,
    avgWinConf: avgConf(wins), avgLossConf: avgConf(losses),
    buyAcc: acc(buyRes),   buyN: buyRes.length,
    sellAcc: acc(sellRes), sellN: sellRes.length,
    hiConfAcc: acc(hiConf), hiConfN: hiConf.length,
    brier, reliability,
  };
}

// ── Re-check signal (validations feedback loop) ──────────────────────────────
// THIS is what "consumes" the validity re-checks: across resolved trades that were
// re-checked at least once, bucket by the LAST re-check's assessment and measure how
// they actually resolved. Answers "does flagging a trade WEAKENING/INVALIDATED predict
// a loss?" — and feeds that learned rate back onto the cards and re-check banners.
// Dormant (n=0) until re-checked trades start resolving.
function computeValidationReliability(rows) {
  const b = { confirmed: { tp: 0, sl: 0 }, weakening: { tp: 0, sl: 0 }, invalidated: { tp: 0, sl: 0 } };
  for (const r of rows) {
    if (r.outcome !== 'tp_hit' && r.outcome !== 'sl_hit') continue;
    const vs = parseValidations(r.validations);
    if (!vs.length) continue;
    const a = vs[vs.length - 1].assessment;   // the last re-check before it resolved
    if (!b[a]) continue;
    if (r.outcome === 'tp_hit') b[a].tp++; else b[a].sl++;
  }
  const out = { total: 0 };
  for (const k of ['confirmed', 'weakening', 'invalidated']) {
    const n = b[k].tp + b[k].sl;
    out[k] = { n, slRate: n ? Math.round(b[k].sl / n * 100) : null, tpRate: n ? Math.round(b[k].tp / n * 100) : null };
    out.total += n;
  }
  return out;
}

// Short learned annotation for a re-check of the given assessment (or '' if too thin).
function reliabilityNote(assessment, minN = 4) {
  const s = _valReliability[assessment];
  if (!s || s.n < minN) return '';
  if (assessment === 'confirmed') return ` · historically ${s.tpRate}% hit target (n=${s.n})`;
  return ` · historically ${s.slRate}% hit stop (n=${s.n})`;
}

function accStat(label, value, sub, cls) {
  return `<div class="acc-stat">
    <span class="acc-val ${cls || ''}">${value}</span>
    <span class="acc-label">${label}</span>
    ${sub ? `<span class="acc-sub">${sub}</span>` : ''}
  </div>`;
}

// Scoreboard scope: 'all' = every scan (the full data the AI calibrates on),
// 'mine' = only the user's own calls (auto-scan rows excluded).
let _scoreScope = 'all';
function setScoreScope(s) { _scoreScope = s; renderScoreboard(); }

function renderScoreboard() {
  const el = document.getElementById('accBoard');
  if (!el) return;
  const autoN  = _allRows.filter(isAuto).length;
  const scoped = _scoreScope === 'mine' ? _allRows.filter(r => !isAuto(r)) : _allRows;
  const a = computeAccuracy(scoped);
  // Only offer the toggle once auto-scans actually exist (otherwise it's noise).
  const scopeToggle = autoN ? `
    <div class="acc-scope">
      <button class="acc-scope-btn ${_scoreScope === 'all' ? 'active' : ''}" onclick="setScoreScope('all')" title="Every scan, including the nightly auto-scans the AI learns from">All${` · ${autoN} auto`}</button>
      <button class="acc-scope-btn ${_scoreScope === 'mine' ? 'active' : ''}" onclick="setScoreScope('mine')" title="Only the calls you ran yourself">My scans</button>
    </div>` : '';
  if (!a.total) {
    el.innerHTML = `<div class="acc-header"><div class="acc-title">🎯 Accuracy Scoreboard</div>${scopeToggle}</div>
      <div class="acc-empty">No ${_scoreScope === 'mine' ? 'personal' : ''} scans yet in this view.</div>`;
    return;
  }

  const wrCls = a.winRate == null ? '' : a.winRate >= 50 ? 'pos' : 'neg';
  const cmp = (a.avgWinConf != null && a.avgLossConf != null)
    ? `${a.avgWinConf}% on wins vs ${a.avgLossConf}% on losses` : '—';
  const cmpCls = (a.avgWinConf != null && a.avgLossConf != null)
    ? (a.avgWinConf >= a.avgLossConf ? 'pos' : 'neg') : '';

  const relRows = (a.reliability || []).map(r => {
    const cls = r.gap == null ? '' : Math.abs(r.gap) <= 10 ? 'pos' : 'neg';
    const w   = r.acc == null ? 0 : Math.max(3, Math.min(100, r.acc));
    return `<div class="acc-rel-row">
      <span class="acc-rel-band">${r.band}%</span>
      <span class="acc-rel-bar"><span class="acc-rel-fill ${cls}" style="width:${w}%"></span></span>
      <span class="acc-rel-val ${cls}">${r.acc}% actual</span>
      <span class="acc-rel-n">n=${r.n}</span>
    </div>`;
  }).join('');

  // Re-check signal — the validations feedback loop made visible.
  const vr = computeValidationReliability(scoped);
  const VR_ROWS = [
    { key: 'confirmed',   label: 'Confirmed',   side: 'target' },
    { key: 'weakening',   label: 'Weakening',   side: 'stop' },
    { key: 'invalidated', label: 'Invalidated', side: 'stop' },
  ];
  const vrRows = VR_ROWS.filter(x => vr[x.key].n > 0).map(x => {
    const s = vr[x.key]; const good = x.side === 'target';
    const rate = good ? s.tpRate : s.slRate; const cls = good ? 'pos' : 'neg';
    return `<div class="acc-rel-row">
      <span class="acc-rel-band">${x.label}</span>
      <span class="acc-rel-bar"><span class="acc-rel-fill ${cls}" style="width:${Math.max(3, rate)}%"></span></span>
      <span class="acc-rel-val ${cls}">${rate}% hit ${good ? 'target' : 'stop'}</span>
      <span class="acc-rel-n">n=${s.n}</span>
    </div>`;
  }).join('');
  const valPanel = `<div class="acc-rel">
    <div class="acc-rel-title">🔁 Re-check signal — does a re-validation predict the outcome?</div>
    ${vr.total ? `<div class="acc-rel-rows">${vrRows}</div>
      <div class="acc-rel-foot">Of re-checked trades that have since resolved, how the LAST re-check's read lined up with reality. A high "Weakening → hit stop" rate means the Update button is a real early-warning — and that signal now shows on each re-checked card.</div>`
      : `<div class="acc-rel-foot">No re-checked trades have resolved yet. Once a trade you hit <strong>Update</strong> on closes (TP or SL), this learns whether "weakening/invalidated" flags actually predict losses — and starts annotating re-checks with that track record.</div>`}
  </div>`;

  el.innerHTML = `
    <div class="acc-header"><div class="acc-title">🎯 Accuracy Scoreboard</div>${scopeToggle}</div>
    <div class="acc-grid">
      ${accStat('Total Scans', a.total, `${a.resolvedN} resolved`, '')}
      ${accStat('% Resolved', a.pctResolved + '%', `${a.total - a.resolvedN} still open`, '')}
      ${accStat('Win Rate', a.winRate != null ? a.winRate + '%' : '—', a.resolvedN ? `${a.wins}W / ${a.losses}L` : 'no resolved calls', wrCls)}
      ${accStat('Conf · Win vs Loss', cmp, 'avg confidence by outcome', cmpCls)}
      ${accStat('BUY Accuracy', a.buyAcc != null ? a.buyAcc + '%' : '—', a.buyN ? `${a.buyN} resolved BUYs` : 'none resolved', a.buyAcc == null ? '' : a.buyAcc >= 50 ? 'pos' : 'neg')}
      ${accStat('SELL Accuracy', a.sellAcc != null ? a.sellAcc + '%' : '—', a.sellN ? `${a.sellN} resolved SELLs` : 'none resolved', a.sellAcc == null ? '' : a.sellAcc >= 50 ? 'pos' : 'neg')}
      ${accStat('Brier Score', a.brier != null ? a.brier.toFixed(3) : '—',
        a.brier == null ? 'need resolved calls' : a.brier <= 0.25 ? 'beats 50/50 guessing' : 'worse than a coin-flip',
        a.brier == null ? '' : a.brier <= 0.25 ? 'pos' : 'neg')}
    </div>
    ${relRows ? `<div class="acc-rel">
      <div class="acc-rel-title">Calibration curve — stated confidence vs. what actually happened</div>
      <div class="acc-rel-rows">${relRows}</div>
      <div class="acc-rel-foot">Bands within ±10% of the diagonal are well-calibrated. Large gaps = over/under-confidence the live verdict now auto-corrects.</div>
    </div>` : ''}
    ${valPanel}
    <div class="acc-calib ${a.hiConfAcc == null ? '' : a.hiConfAcc >= 50 ? 'pos' : 'neg'}">
      When APEX says <strong>80%+ confidence</strong> →
      ${a.hiConfN ? `<strong>${a.hiConfAcc}% accuracy</strong> across ${a.hiConfN} resolved high-conviction call${a.hiConfN === 1 ? '' : 's'}` : 'no resolved 80%+ calls yet'}
    </div>`;
}

// ── View toggle (Scans ⟷ Watchlist) ─────────────────────────────────────────
let _currentView = 'scans';

function setView(view) {
  _currentView = view;
  document.querySelectorAll('.vt-btn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  const isScans = view === 'scans';
  document.querySelector('.hist-filters').style.display = isScans ? '' : 'none';
  document.getElementById('scanGrid').style.display     = isScans ? '' : 'none';
  document.getElementById('accBoard').style.display     = isScans ? '' : 'none';
  const empty = document.getElementById('histEmpty');
  if (empty && !isScans) empty.style.display = 'none';
  document.getElementById('watchlistView').style.display = isScans ? 'none' : '';
  if (isScans) renderGrid(); else renderWatchlist();
}

function initViewToggle() {
  document.querySelectorAll('.vt-btn').forEach(btn => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });
}

// ── Watchlist (localStorage) ─────────────────────────────────────────────────
const WL_KEY    = 'apex_watchlist';
const ALERT_KEY = 'apex_alerts';

function getWatchlist() { try { return JSON.parse(localStorage.getItem(WL_KEY) || '[]'); } catch { return []; } }
function setWatchlistStore(l) { try { localStorage.setItem(WL_KEY, JSON.stringify(l)); } catch {} }
function getAlerts() { try { return JSON.parse(localStorage.getItem(ALERT_KEY) || '{}'); } catch { return {}; } }
function setAlertsStore(a) { try { localStorage.setItem(ALERT_KEY, JSON.stringify(a)); } catch {} }

const _wlPrices = {};   // sym → latest close, cached across renders

async function fetchLivePrice(sym, type) {
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 7 * 86400;
    const r = await fetch(`${API_CANDLES}?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type || 'Stock')}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return null;
    const c = await r.json();
    if (!Array.isArray(c) || !c.length) return null;
    return c[c.length - 1].close;
  } catch { return null; }
}

// Parse the low/high bounds of an entry zone like "182.5 - 185" or "182.5".
function entryBounds(entryZone) {
  const nums = String(entryZone == null ? '' : entryZone).match(/-?\d+(?:\.\d+)?/g);
  if (!nums) return null;
  const v = nums.map(Number).filter(n => !isNaN(n));
  if (!v.length) return null;
  return { lo: Math.min(...v), hi: Math.max(...v) };
}

// Classify a watchlist row given its live price.
function rowStatus(item, price) {
  if (price == null) return { cls: 'pending', label: '⏳ —' };
  const dir = verdictDir(item.verdict);
  const sl  = parseFloat(item.stop_loss);
  const tp  = parseFloat(item.target_price);
  const eb  = entryBounds(item.entry_zone);

  // Stop hit?
  if (!isNaN(sl)) {
    if (dir === 'short' && price >= sl) return { cls: 'stopped', label: '❌ Stop hit' };
    if (dir !== 'short' && price <= sl) return { cls: 'stopped', label: '❌ Stop hit' };
  }
  // Target hit?
  if (!isNaN(tp)) {
    if (dir === 'short' && price <= tp) return { cls: 'target', label: '✅ Target hit' };
    if (dir !== 'short' && price >= tp) return { cls: 'target', label: '✅ Target hit' };
  }
  // In the entry zone?
  if (eb) {
    const pad = (eb.hi - eb.lo) * 0.001 + Math.abs(eb.hi) * 0.0015;   // small tolerance
    if (price >= eb.lo - pad && price <= eb.hi + pad) return { cls: 'inzone', label: '🟢 In entry zone' };
  }
  return { cls: 'pending', label: '⏳ Pending' };
}

function renderWatchlist() {
  const list = getWatchlist();
  const wrap  = document.getElementById('wlTableWrap');
  const empty = document.getElementById('wlEmpty');
  const body  = document.getElementById('wlBody');
  if (!list.length) {
    wrap.style.display = 'none';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  wrap.style.display = '';

  const alerts = getAlerts();
  body.innerHTML = list.map((item, i) => {
    const price = _wlPrices[item.sym];
    const st = rowStatus(item, price);
    const vc = verdictClass(item.verdict);
    const hasAlert = alerts[item.sym] != null;
    return `
      <tr class="wl-row ${st.cls}">
        <td class="wl-sym">${escHtml(item.sym)}<span class="wl-type">${escHtml(item.type || '')}</span></td>
        <td><span class="wl-verdict ${vc}">${escHtml((item.verdict || '—').replace(/_/g, ' '))}</span></td>
        <td class="wl-mono">${item.confidence != null ? item.confidence + '%' : '—'}</td>
        <td class="wl-mono">${item.entry_zone ? escHtml(String(item.entry_zone)) : '—'}</td>
        <td class="wl-mono stop">${item.stop_loss ? fmtPrice(item.stop_loss) : '—'}</td>
        <td class="wl-mono target">${item.target_price ? fmtPrice(item.target_price) : '—'}</td>
        <td class="wl-mono now">${price != null ? fmtPrice(price) : '…'}</td>
        <td><span class="wl-status ${st.cls}">${st.label}</span></td>
        <td class="wl-actions">
          <button class="wl-btn ${hasAlert ? 'on' : ''}" onclick="setAlert(${i})" title="${hasAlert ? 'Alert set at ' + alerts[item.sym] : 'Set a price alert'}">🔔${hasAlert ? '✓' : ''}</button>
          <button class="wl-btn del" onclick="removeFromWatchlist(${i})" title="Remove">✕</button>
        </td>
      </tr>`;
  }).join('');

  // Fetch any missing live prices, then re-render once they land
  const missing = list.filter(item => _wlPrices[item.sym] == null);
  if (missing.length) {
    Promise.allSettled(missing.map(async item => {
      const p = await fetchLivePrice(item.sym, item.type);
      if (p != null) _wlPrices[item.sym] = p;
    })).then(() => { if (_currentView === 'watchlist') renderWatchlist(); checkAlerts(); });
  }

  // Portfolio heat / correlation (needs ≥2 holdings)
  loadPortfolioHeat(list);
}

// ── Portfolio heat / correlation ─────────────────────────────────────────────
// Highly-correlated holdings are effectively ONE position at multiplied size — the
// classic risk that "5 different longs" are really 5× the same beta. We fetch each
// holding's recent daily returns and surface pairs that move together.
const _wlReturns = {};   // sym → array of daily returns (cached)

async function fetchReturnSeries(sym, type) {
  if (_wlReturns[sym]) return _wlReturns[sym];
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 130 * 86400;
    const r = await fetch(`${API_CANDLES}?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type || 'Stock')}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return null;
    const c = await r.json();
    if (!Array.isArray(c) || c.length < 20) return null;
    const closes = c.map(b => b.close).filter(x => x != null);
    const rets = closes.slice(1).map((v, i) => (v - closes[i]) / closes[i]);
    _wlReturns[sym] = rets;
    return rets;
  } catch { return null; }
}

function pearson(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 15) return null;
  const x = a.slice(-n), y = b.slice(-n);
  const mx = x.reduce((s, v) => s + v, 0) / n, my = y.reduce((s, v) => s + v, 0) / n;
  let num = 0, dx = 0, dy = 0;
  for (let i = 0; i < n; i++) { const a1 = x[i] - mx, b1 = y[i] - my; num += a1 * b1; dx += a1 * a1; dy += b1 * b1; }
  const den = Math.sqrt(dx * dy);
  return den > 0 ? num / den : null;
}

async function loadPortfolioHeat(list) {
  const el = document.getElementById('wlHeat');
  if (!el) return;
  if (!list || list.length < 2) { el.innerHTML = ''; return; }

  await Promise.allSettled(list.map(item => fetchReturnSeries(item.sym, item.type)));
  if (_currentView !== 'watchlist') return;

  // Pairwise correlations
  const pairs = [];
  const involved = new Set();
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = _wlReturns[list[i].sym], b = _wlReturns[list[j].sym];
      if (!a || !b) continue;
      const c = pearson(a, b);
      if (c == null) continue;
      pairs.push({ a: list[i].sym, b: list[j].sym, c });
      if (Math.abs(c) >= 0.7) { involved.add(list[i].sym); involved.add(list[j].sym); }
    }
  }
  if (!pairs.length) { el.innerHTML = ''; return; }

  const high = pairs.filter(p => Math.abs(p.c) >= 0.7).sort((a, b) => Math.abs(b.c) - Math.abs(a.c));
  const corrClass = c => Math.abs(c) >= 0.7 ? 'hot' : Math.abs(c) >= 0.4 ? 'warm' : 'cool';
  const top = pairs.slice().sort((a, b) => Math.abs(b.c) - Math.abs(a.c)).slice(0, 6);

  let warn = '';
  if (involved.size >= 2) {
    warn = `<div class="wl-heat-warn">⚠️ ${involved.size} of your ${list.length} holdings are highly correlated (≥0.7) — they move as effectively one position, so your real risk is more concentrated than the count suggests. Consider sizing them as a group.</div>`;
  }

  el.innerHTML = `
    <div class="wl-heat-title">🔥 Portfolio Heat — correlation of recent daily moves</div>
    ${warn}
    <div class="wl-heat-pairs">
      ${top.map(p => `<span class="wl-corr ${corrClass(p.c)}">${escHtml(p.a)} · ${escHtml(p.b)} <strong>${p.c >= 0 ? '+' : ''}${p.c.toFixed(2)}</strong></span>`).join('')}
    </div>`;
}

function removeFromWatchlist(i) {
  const list = getWatchlist();
  if (i < 0 || i >= list.length) return;
  const removed = list[i];
  list.splice(i, 1);
  setWatchlistStore(list);
  // Drop any alert tied to a symbol no longer on the list
  if (removed && !list.some(x => x.sym === removed.sym)) {
    const alerts = getAlerts(); delete alerts[removed.sym]; setAlertsStore(alerts);
  }
  renderWatchlist();
}

// Set / clear a price-alert threshold for a watchlist row.
function setAlert(i) {
  const list = getWatchlist();
  const item = list[i];
  if (!item) return;
  const alerts = getAlerts();
  if (alerts[item.sym] != null) {            // toggle off if already set
    delete alerts[item.sym];
    setAlertsStore(alerts);
    renderWatchlist();
    return;
  }
  const eb = entryBounds(item.entry_zone);
  const suggested = eb ? ((eb.lo + eb.hi) / 2) : (_wlPrices[item.sym] ?? item.currentPrice ?? '');
  const input = window.prompt(`Set a price alert for ${item.sym}.\nYou'll be notified when the price reaches this level:`, suggested ? String(+(+suggested).toFixed(5)) : '');
  if (input == null) return;
  const threshold = parseFloat(input);
  if (isNaN(threshold)) return;
  const ref = _wlPrices[item.sym] ?? parseFloat(item.currentPrice) ?? threshold;
  alerts[item.sym] = threshold;
  // Remember which side of the threshold we started on, so we can detect a crossing
  alerts[`${item.sym}__from`] = ref >= threshold ? 'above' : 'below';
  setAlertsStore(alerts);
  renderWatchlist();
  checkAlerts();
}

// On load / focus, fire any alert whose threshold has been reached.
function checkAlerts() {
  const alerts = getAlerts();
  const list = getWatchlist();
  const triggered = [];
  for (const item of list) {
    const th = alerts[item.sym];
    if (th == null) continue;
    const price = _wlPrices[item.sym];
    if (price == null) continue;
    const from = alerts[`${item.sym}__from`];
    const reached = from === 'above' ? price <= th : from === 'below' ? price >= th
      : Math.abs(price - th) / (Math.abs(th) || 1) < 0.002;
    if (reached) triggered.push({ sym: item.sym, th, price });
  }
  renderAlertBanner(triggered);
}

function renderAlertBanner(triggered) {
  const el = document.getElementById('alertBanner');
  if (!el) return;
  if (!triggered.length) { el.innerHTML = ''; return; }
  el.innerHTML = triggered.map(t =>
    `<div class="alert-banner">⚠️ <strong>${escHtml(t.sym)}</strong> has reached your alert level (${fmtPrice(t.th)}) — now ${fmtPrice(t.price)}.
      <button class="alert-dismiss" onclick="dismissAlert('${escHtml(t.sym)}')">Dismiss</button></div>`
  ).join('');
}

function dismissAlert(sym) {
  const alerts = getAlerts();
  delete alerts[sym];
  delete alerts[`${sym}__from`];
  setAlertsStore(alerts);
  checkAlerts();
  if (_currentView === 'watchlist') renderWatchlist();
}

// Pre-fetch watchlist prices so the alert banner can fire on load (any view).
async function primeWatchlistPrices() {
  const list = getWatchlist();
  if (!list.length) return;
  await Promise.allSettled(list.map(async item => {
    const p = await fetchLivePrice(item.sym, item.type);
    if (p != null) _wlPrices[item.sym] = p;
  }));
  checkAlerts();
}

// Refresh live prices when the tab regains focus (lightweight polling).
function refreshOnFocus() {
  window.addEventListener('focus', () => {
    const list = getWatchlist();
    if (!list.length) return;
    Promise.allSettled(list.map(async item => {
      const p = await fetchLivePrice(item.sym, item.type);
      if (p != null) _wlPrices[item.sym] = p;
    })).then(() => { checkAlerts(); if (_currentView === 'watchlist') renderWatchlist(); });
  });
}

// Re-fetch scans + re-render. So a re-check / scan run elsewhere shows up when you
// come back to this tab, without a manual reload (the stale-page complaint).
let _lastScanLoad = Date.now();
async function reloadScans() {
  try {
    _allRows = await fetchAllScans();
    indexRows();
    _lastScanLoad = Date.now();
    updateSummary(); renderScoreboard(); if (_currentView === 'scans') renderGrid();
    resolveIfPending(_allRows)
      .then(() => generateLessons(_allRows))
      .then(() => { indexRows(); updateSummary(); renderScoreboard(); if (_currentView === 'scans') renderGrid(); })
      .catch(() => {});
    loadOpenTradePrices();
  } catch {}
}
function initAutoRefresh() {
  // bfcache restore (browser Back), tab becoming visible, or window focus — refresh.
  window.addEventListener('pageshow', (e) => { if (e.persisted) reloadScans(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - _lastScanLoad > 4000) reloadScans();
  });
  window.addEventListener('focus', () => { if (Date.now() - _lastScanLoad > 4000) reloadScans(); });
}

// ── Post-mortem lessons ──────────────────────────────────────────────────────
// When a trade resolves, generate a short "what went right / wrong" lesson and
// persist it. The live committee later retrieves lessons from STRUCTURALLY-similar
// setups (see dashboard.js fetchLessons) and feeds them into new verdicts, so the
// engine learns from each closed trade instead of repeating the mistake.

// Minimal AI caller (mirrors dashboard.js callAgent; one retry on a transient 5xx).
async function callAgent(system, prompt, maxTokens = 400) {
  const attempt = async () => {
    const res = await fetch('/api/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, system, max_tokens: maxTokens, temperature: 0.3, timeoutMs: 55000 }),
    });
    const raw = await res.text();
    let data = null;
    try { data = raw ? JSON.parse(raw) : {}; } catch { data = null; }
    if (data === null) { const e = new Error('AI hiccup'); e._transient = res.status >= 500 || res.status === 0; throw e; }
    if (!res.ok || data.error) { const e = new Error(data.error || `HTTP ${res.status}`); e._transient = res.status >= 500; throw e; }
    return data.text || '';
  };
  try { return await attempt(); }
  catch (e) { if (e._transient) { await new Promise(r => setTimeout(r, 1500)); return await attempt(); } throw e; }
}

function outcomePlain(o) {
  if (o === 'tp_hit')  return 'WON — price reached the target before the stop';
  if (o === 'sl_hit')  return 'LOST — price hit the stop-loss before the target';
  if (o === 'expired') return 'EXPIRED — neither target nor stop was reached within the trade window';
  return 'unresolved';
}

// Pull a one/two-sentence lesson out of the model's reply (tolerates raw text or JSON).
function parseLesson(text) {
  if (!text) return null;
  const m = text.match(/\{[\s\S]*\}/);
  if (m) { try { const o = JSON.parse(m[0]); if (o && o.lesson) return String(o.lesson).trim(); } catch {} }
  return String(text).replace(/^["'\s]+|["'\s]+$/g, '').slice(0, 500) || null;
}

async function generateLessonFor(row) {
  const reasons = parseReasons(row.key_reasons);
  const heldDays = Math.max(0, Math.round((rowTs(row) ? (Date.now() - rowTs(row)) / 86400000 : 0)));
  const system = 'You are a blunt trading post-mortem analyst. You review a CLOSED trade idea against what actually happened and extract the single most useful, transferable lesson. Be specific and honest — name the mistake if there was one. Reply ONLY with strict JSON: {"lesson":"<1-2 sentences>"}.';
  const prompt = `CLOSED TRADE on ${row.symbol} (${row.asset_type || 'Stock'}).
Original call: ${(row.verdict || '').replace(/_/g, ' ')} at ${row.confidence != null ? row.confidence + '% confidence' : 'unknown confidence'}.
Entry zone: ${row.entry_zone || '—'} | Target: ${row.target_price || '—'} | Stop: ${row.stop_loss || '—'} | R:R: ${row.risk_reward || '—'}.
Scan price: ${row.price || '—'}. Held ~${heldDays} day(s).
Original thesis (key reasons): ${reasons.length ? reasons.map(r => '• ' + r).join(' ') : (row.summary || '—')}
Technical read at the time: ${row.technical_analysis || '—'}

ACTUAL RESULT: ${outcomePlain(row.outcome)}.

Write the lesson: what did the thesis get right or wrong, and the ONE thing to watch for on a structurally-similar setup next time? Strict JSON only.`;
  const text = await callAgent(system, prompt, 400);
  return parseLesson(text);
}

// Generate + persist lessons for newly-resolved rows that don't have one yet.
// Capped per page load so we never fan out a huge batch of AI calls at once.
async function generateLessons(rows, cap = 4) {
  const need = rows.filter(r =>
    (r.outcome === 'tp_hit' || r.outcome === 'sl_hit' || r.outcome === 'expired') &&
    (r.lesson == null || r.lesson === '')
  ).slice(0, cap);
  if (!need.length) return false;

  let any = false;
  await Promise.allSettled(need.map(async (row) => {
    try {
      const lesson = await generateLessonFor(row);
      if (!lesson) return;
      row.lesson = lesson;
      any = true;
      fetch(API_MEMORY, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: row.id, lesson }),
      }).catch(() => {});
    } catch {}
  }));
  return any;
}

// Fetch live prices for every OPEN trade's symbol so the cards can show how far
// price is from the entry zone, then re-render. Cheap (last close per symbol), no AI.
async function loadOpenTradePrices() {
  const open = {};
  for (const r of _allRows) {
    if ((r.outcome == null || r.outcome === 'pending') && r.entry_zone) open[r.symbol] = r.asset_type || 'Stock';
  }
  const syms = Object.keys(open).filter(s => _livePx[s] == null);
  if (!syms.length) return;
  await Promise.allSettled(syms.map(async s => { const p = await fetchLivePrice(s, open[s]); if (p != null) _livePx[s] = p; }));
  if (_currentView === 'scans') renderGrid();
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function init() {
  const loadingEl = document.getElementById('histLoading');

  try {
    _allRows = await fetchAllScans();
    indexRows();

    // Resolve outcomes in background, THEN generate post-mortem lessons for anything
    // newly closed (and any older resolved row still missing one), then re-render.
    resolveIfPending(_allRows)
      .then(() => generateLessons(_allRows))
      .then(() => {
        indexRows();
        updateSummary();
        renderScoreboard();
        if (_currentView === 'scans') renderGrid();
      }).catch(() => {});

    loadingEl.style.display = 'none';
    updateSummary();
    renderScoreboard();
    renderGrid();
    _lastScanLoad = Date.now();
    initFilters();
    initViewToggle();
    initGridActions();
    refreshOnFocus();
    initAutoRefresh();        // re-fetch when you return to the tab (no stale view)
    primeWatchlistPrices();   // so an alert can fire on load even from the Scans view
    loadOpenTradePrices();    // distance-to-entry indicator on open trades
  } catch (err) {
    loadingEl.innerHTML = `<p style="color:#f87171">Failed to load history: ${err.message}</p>`;
  }
}

document.addEventListener('DOMContentLoaded', init);
