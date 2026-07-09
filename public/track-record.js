// Public, site-wide, loss-included track record of the ENGINE's resolved calls.
// Reads every logged verdict from /api/memory (all symbols, auto + manual) and shows
// the honest aggregate: win rate, BUY/SELL accuracy, a Brier score, the calibration
// curve (does stated confidence hold up?), and a recent wins-AND-losses log. This is
// APEX's moat made verifiable — nothing here is cherry-picked.
(function () {
  initPulse();
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const dirOf = (r) => { const u = (r.verdict || '').toUpperCase(); if (/BUY|LONG/.test(u)) return 'buy'; if (/SELL|SHORT/.test(u)) return 'sell'; return 'other'; };

  // Parse "1:2.5" / "2.5:1" / "2.5" into reward-per-1-unit-risk (mirrors dashboard.js).
  function parseRewardRisk(rr) {
    if (rr == null) return null;
    const s = String(rr).trim();
    const m = s.match(/(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)/);
    if (m) {
      const a = parseFloat(m[1]), b = parseFloat(m[2]);
      if (!(a > 0) || !(b > 0)) return null;
      return a === 1 ? b : (b === 1 ? a : b / a);
    }
    const n = parseFloat(s);
    return isFinite(n) && n > 0 ? n : null;
  }

  // Profitability in R-MULTIPLES — the honest way to score a varied-RR trade log: a
  // loss is always -1R (you lost exactly what you risked); a win is the stated
  // reward:risk (e.g. a 3:1 win = +3R). Expectancy = avg R per trade; if positive,
  // sizing every trade at a fixed 1% risk would have grown the account over the
  // window. EXPIRED trades are excluded (no real fill -> no real P&L to score), not
  // counted as either a win or a loss.
  function computeProfitability(rows, sinceMs) {
    const resolved = rows.filter((r) =>
      (r.outcome === 'tp_hit' || r.outcome === 'sl_hit') &&
      (sinceMs == null || Date.parse(r.outcome_date || r.created_at || 0) >= sinceMs));
    if (!resolved.length) return { n: 0 };
    let totalR = 0, wins = 0, rrSum = 0, rrN = 0;
    for (const r of resolved) {
      const rr = parseRewardRisk(r.risk_reward);
      if (rr != null) { rrSum += rr; rrN++; }
      if (r.outcome === 'tp_hit') { wins++; totalR += (rr != null ? rr : 1); }
      else { totalR -= 1; }
    }
    const n = resolved.length;
    const expectancy = +(totalR / n).toFixed(2);
    return {
      n, wins, losses: n - wins,
      winRate: Math.round(wins / n * 100),
      avgRR: rrN ? +(rrSum / rrN).toFixed(2) : null,
      expectancy, totalR: +totalR.toFixed(1),
      profitable: expectancy > 0,
    };
  }

  function compute(rows) {
    const resolved = rows.filter((r) => r.outcome === 'tp_hit' || r.outcome === 'sl_hit');
    const wins = resolved.filter((r) => r.outcome === 'tp_hit');
    const winRate = resolved.length ? Math.round(wins.length / resolved.length * 100) : null;
    const buy = resolved.filter((r) => dirOf(r) === 'buy'), sell = resolved.filter((r) => dirOf(r) === 'sell');
    const acc = (s) => s.length ? Math.round(s.filter((r) => r.outcome === 'tp_hit').length / s.length * 100) : null;

    let brier = null;
    if (resolved.length) {
      const sum = resolved.reduce((s, r) => {
        const p = Math.min(1, Math.max(0, (Number(r.confidence) || 50) / 100));
        const o = r.outcome === 'tp_hit' ? 1 : 0;
        return s + (p - o) * (p - o);
      }, 0);
      brier = +(sum / resolved.length).toFixed(3);
    }

    const mid = { '50–59': 55, '60–69': 65, '70–79': 75, '80–89': 85, '90+': 95 };
    const rel = [
      { b: '50–59', lo: 0, hi: 59 }, { b: '60–69', lo: 60, hi: 69 }, { b: '70–79', lo: 70, hi: 79 },
      { b: '80–89', lo: 80, hi: 89 }, { b: '90+', lo: 90, hi: 100 },
    ].map((x) => {
      const set = resolved.filter((r) => { const c = Number(r.confidence) || 0; return c >= x.lo && c <= x.hi; });
      const a = set.length ? Math.round(set.filter((r) => r.outcome === 'tp_hit').length / set.length * 100) : null;
      return { band: x.b, n: set.length, acc: a, gap: a == null ? null : a - mid[x.b] };
    }).filter((x) => x.n > 0);

    const recent = resolved.slice().sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))).slice(0, 40);
    return { total: rows.length, resolvedN: resolved.length, winRate, wins: wins.length, losses: resolved.length - wins.length,
      buyAcc: acc(buy), buyN: buy.length, sellAcc: acc(sell), sellN: sell.length, brier, rel, recent };
  }

  // ── Profitability card (week / month / lifetime tabs) ──────────────────────
  const DAY_MS = 86400000;
  const PROFIT_WINDOWS = { week: 7 * DAY_MS, month: 30 * DAY_MS, lifetime: null };
  let _allRowsCache = [];
  let _profitWindow = 'lifetime';

  function renderProfitCard() {
    const el = $('trProfit');
    if (!el) return;
    const sinceMs = PROFIT_WINDOWS[_profitWindow] != null ? Date.now() - PROFIT_WINDOWS[_profitWindow] : null;
    const p = computeProfitability(_allRowsCache, sinceMs);
    const tabs = Object.keys(PROFIT_WINDOWS).map((w) =>
      `<button class="tr-tab ${w === _profitWindow ? 'active' : ''}" data-window="${w}">${w[0].toUpperCase()}${w.slice(1)}</button>`).join('');

    if (!p.n) {
      el.innerHTML = `<div class="tr-profit-head"><div class="tr-section-title" style="margin:0">Profitability</div><div class="tr-tabs">${tabs}</div></div>
        <div class="tr-empty" style="padding:18px">No calls resolved in this window yet.</div>`;
    } else {
      const cls = p.profitable ? 'pos' : 'neg';
      el.innerHTML = `
        <div class="tr-profit-head"><div class="tr-section-title" style="margin:0">Profitability</div><div class="tr-tabs">${tabs}</div></div>
        <div class="tr-profit-banner ${cls}">
          <span class="tr-profit-badge ${cls}">${p.profitable ? '✓ Profitable' : '✗ Not profitable'}</span>
          <span class="tr-profit-sub">${p.expectancy > 0 ? '+' : ''}${p.expectancy}R expectancy per trade — risking a fixed 1% per trade over this window would have returned ${p.totalR > 0 ? '+' : ''}${p.totalR}% (${p.n} resolved trade${p.n === 1 ? '' : 's'})</span>
        </div>
        <div class="acc-grid">
          ${`<div class="acc-stat"><span class="acc-val">${p.n}</span><span class="acc-label">Resolved</span><span class="acc-sub">${p.wins}W / ${p.losses}L</span></div>`}
          ${`<div class="acc-stat"><span class="acc-val ${p.winRate >= 50 ? 'pos' : 'neg'}">${p.winRate}%</span><span class="acc-label">Win Rate</span></div>`}
          ${`<div class="acc-stat"><span class="acc-val">${p.avgRR != null ? p.avgRR.toFixed(1) + ':1' : '—'}</span><span class="acc-label">Avg R:R</span><span class="acc-sub">stated, on resolved calls</span></div>`}
          ${`<div class="acc-stat"><span class="acc-val ${cls}">${p.expectancy > 0 ? '+' : ''}${p.expectancy}R</span><span class="acc-label">Expectancy</span><span class="acc-sub">avg realised R per trade</span></div>`}
          ${`<div class="acc-stat"><span class="acc-val ${cls}">${p.totalR > 0 ? '+' : ''}${p.totalR}R</span><span class="acc-label">Total R</span><span class="acc-sub">sum, this window</span></div>`}
        </div>
        <div class="tr-note">A loss always counts as -1R (you lost exactly what you risked); a win counts as the stated reward:risk (e.g. a 3:1 win = +3R). Expired/never-filled calls are excluded — no real position, no real P&L.</div>`;
    }
    el.querySelectorAll('.tr-tab').forEach((b) => b.addEventListener('click', () => {
      _profitWindow = b.dataset.window; renderProfitCard();
    }));
  }

  function render(a, rawCount) {
    if (!a.resolvedN) {
      $('trBody').innerHTML = `<div class="tr-empty"><strong>${rawCount} call${rawCount === 1 ? '' : 's'} logged · 0 resolved yet.</strong><br><br>The engine scans daily and records every verdict. Each call resolves only once price reaches the take-profit or stop-loss level it stated — that takes real market time, so this record fills in over the coming days and weeks. When it populates you'll see every win <em>and</em> loss, no cherry-picking.</div>`;
      return;
    }
    const stat = (label, val, sub, cls) => `<div class="acc-stat"><span class="acc-val ${cls || ''}">${val}</span><span class="acc-label">${label}</span>${sub ? `<span class="acc-sub">${sub}</span>` : ''}</div>`;
    const relRows = (a.rel || []).map((r) => {
      const cls = r.gap == null ? '' : Math.abs(r.gap) <= 10 ? 'pos' : 'neg';
      const w = r.acc == null ? 0 : Math.max(3, Math.min(100, r.acc));
      return `<div class="acc-rel-row"><span class="acc-rel-band">${r.band}%</span><span class="acc-rel-bar"><span class="acc-rel-fill ${cls}" style="width:${w}%"></span></span><span class="acc-rel-val ${cls}">${r.acc}% actual</span><span class="acc-rel-n">n=${r.n}</span></div>`;
    }).join('');
    const rows = a.recent.map((r) => {
      const win = r.outcome === 'tp_hit';
      return `<tr><td>${esc((r.created_at || r.analysis_date || '').slice(0, 10))}</td><td>${esc(r.symbol)}</td><td>${esc(r.verdict)}</td><td>${r.confidence != null ? r.confidence + '%' : '—'}</td><td class="${win ? 'tr-win' : 'tr-loss'}">${win ? '✓ TP hit' : '✗ SL hit'}</td></tr>`;
    }).join('');

    $('trBody').innerHTML = `
      <div class="acc-grid">
        ${stat('Resolved Calls', a.resolvedN, `${a.total} logged total`, '')}
        ${stat('Win Rate', a.winRate + '%', `${a.wins}W / ${a.losses}L`, a.winRate >= 50 ? 'pos' : 'neg')}
        ${stat('BUY Accuracy', a.buyAcc != null ? a.buyAcc + '%' : '—', `${a.buyN} resolved`, a.buyAcc == null ? '' : a.buyAcc >= 50 ? 'pos' : 'neg')}
        ${stat('SELL Accuracy', a.sellAcc != null ? a.sellAcc + '%' : '—', `${a.sellN} resolved`, a.sellAcc == null ? '' : a.sellAcc >= 50 ? 'pos' : 'neg')}
        ${stat('Brier Score', a.brier != null ? a.brier.toFixed(3) : '—', a.brier == null ? 'need data' : a.brier <= 0.25 ? 'beats 50/50' : 'worse than coin-flip', a.brier == null ? '' : a.brier <= 0.25 ? 'pos' : 'neg')}
      </div>
      ${relRows ? `<div class="tr-section-title">Calibration — does the stated confidence hold up?</div><div class="acc-rel"><div class="acc-rel-rows">${relRows}</div><div class="tr-note">For each stated-confidence band, this is how often those calls actually hit target. Bands within ±10% of the diagonal are well-calibrated; large gaps are over- or under-confidence (which the live verdict now auto-corrects).</div></div>` : ''}
      <div class="tr-section-title">Recent resolved calls — wins and losses</div>
      <div style="overflow-x:auto"><table class="tr-table"><thead><tr><th>Date</th><th>Symbol</th><th>Verdict</th><th>Stated conf.</th><th>Outcome</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="tr-note">Every resolved call is shown — no cherry-picking. A verdict resolves when price reaches the take-profit (✓) or stop-loss (✗) it stated.</div>`;
  }

  fetch('/api/memory?all=true&lean=true&limit=1000')
    .then((r) => r.ok ? r.json() : [])
    .then((rows) => {
      if (!Array.isArray(rows)) rows = [];
      _allRowsCache = rows;
      renderProfitCard();
      render(compute(rows), rows.length);
      const el = $('trAsOf');
      if (el) el.textContent = 'Live — read directly from the database on every page load.';
    })
    .catch(() => { $('trBody').innerHTML = '<div class="tr-empty">Could not load the track record right now. Please refresh in a moment.</div>'; });
})();


// ── Market pulse ──────────────────────────────────────────────────────────────
async function loadPulse(sym, type, elId) {
  const elements = document.getElementsByClassName(elId);
  if (!elements.length) return;

  // Render from cache instantly if available (eliminates '-' blink on tab change)
  const cached = localStorage.getItem('pulse_cache_' + sym);
  if (cached) {
    try {
      const data = JSON.parse(cached);
      for (let el of elements) {
        el.classList.remove('loading');
        el.querySelector('.pulse-price').textContent = data.price;
        const ce = el.querySelector('.pulse-change');
        ce.textContent = data.change;
        ce.className = `pulse-change ${data.isUp ? 'up' : 'down'}`;
      }
    } catch {}
  }

  try {
    const to = Math.floor(Date.now() / 1000);
    const from = to - 7 * 86400; // 7 days in seconds to ensure we get at least 2 trading bars
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return;
    const bars = await r.json();
    if (!Array.isArray(bars) || bars.length < 2) return;

    const curr = bars[bars.length - 1].close, prev = bars[bars.length - 2].close;
    const pct = (curr - prev) / prev * 100;
    
    const formattedPrice = type === 'Forex' ? curr.toFixed(5) : curr >= 100 ? curr.toFixed(2) : curr.toFixed(4);
    const formattedChange = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    const isUp = pct >= 0;

    // Save to cache
    localStorage.setItem('pulse_cache_' + sym, JSON.stringify({
      price: formattedPrice,
      change: formattedChange,
      isUp: isUp
    }));

    for (let el of elements) {
      el.classList.remove('loading');
      el.querySelector('.pulse-price').textContent = formattedPrice;
      const ce = el.querySelector('.pulse-change');
      ce.textContent = formattedChange;
      ce.className = `pulse-change ${isUp ? 'up' : 'down'}`;
      if (typeof quickPick === 'function') {
        el.onclick = () => quickPick(sym);
      }
    }
  } catch {}
}
function initPulse() {
  loadPulse('SPY',     'ETF',     'pulse-SPY');
  loadPulse('QQQ',     'ETF',     'pulse-QQQ');
  loadPulse('BTC/USD', 'Crypto',  'pulse-BTC');
  loadPulse('EUR/USD', 'Forex',   'pulse-EUR');
  loadPulse('GC1!',    'Futures', 'pulse-GOLD');
}
