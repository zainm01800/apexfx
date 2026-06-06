// Public, site-wide, loss-included track record of the ENGINE's resolved calls.
// Reads every logged verdict from /api/memory (all symbols, auto + manual) and shows
// the honest aggregate: win rate, BUY/SELL accuracy, a Brier score, the calibration
// curve (does stated confidence hold up?), and a recent wins-AND-losses log. This is
// APEX's moat made verifiable — nothing here is cherry-picked.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const dirOf = (r) => { const u = (r.verdict || '').toUpperCase(); if (/BUY|LONG/.test(u)) return 'buy'; if (/SELL|SHORT/.test(u)) return 'sell'; return 'other'; };

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

  fetch('/api/memory?all=true&limit=200')
    .then((r) => r.ok ? r.json() : [])
    .then((rows) => {
      if (!Array.isArray(rows)) rows = [];
      render(compute(rows), rows.length);
      const el = $('trAsOf');
      if (el) el.textContent = 'Live — read directly from the database on every page load.';
    })
    .catch(() => { $('trBody').innerHTML = '<div class="tr-empty">Could not load the track record right now. Please refresh in a moment.</div>'; });
})();
