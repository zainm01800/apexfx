import { PROFILES, summarize, money as formatMoney, percent, signClass, escapeHtml as e, dateLabel, firstNumber } from './forward-model.js';
import { BOOKS, summarizeLegacy } from './legacy-forward-model.js';

const $ = id => document.getElementById(id);
async function get(book) {
  const r = await fetch(`/api/paper?table=state&book=${book}&limit=500`, { cache: 'no-store' });
  if (!r.ok) throw new Error('Saved ledger unavailable');
  return r.json();
}

async function load() {
  $('refreshCompare').disabled = true;
  const activeBooks = ['s', 'v24', 'v30', 'v6', 'v10'];
  
  const bookData = await Promise.all(activeBooks.map(async book => {
    const isLegacy = BOOKS[book]?.legacy;
    const p = BOOKS[book] || PROFILES[book] || {};
    try {
      const raw = await get(book);
      const m = isLegacy ? summarizeLegacy(raw, book) : summarize(raw, book);
      return {
        book,
        name: p.name || `Book ${book.toUpperCase()}`,
        currency: p.currency || (isLegacy ? 'USD' : 'GBP'),
        pnl: m.pnl ?? 0,
        equity: m.equity ?? 100000,
        maxDD: m.maxDD,
        openPositions: m.payload?.positions?.length ?? 0,
        closedTrades: m.payload?.trades?.length ?? 0,
        status: m.meta?.status || m.state?.status || 'Paper only',
        sessions: m.sessions ?? 0,
        through: m.through,
        activation: m.activation,
        maximum: p.maximum ?? 0.10,
        error: false
      };
    } catch (err) {
      return {
        book,
        name: p.name || `Book ${book.toUpperCase()}`,
        currency: p.currency || 'GBP',
        pnl: -999999,
        equity: 100000,
        maximum: p.maximum ?? 0.10,
        activation: '2026-01-01',
        error: true
      };
    }
  }));

  // Rank books: Most profit first (highest pnl), then newest activation
  bookData.sort((a, b) => {
    const pnlDiff = (b.pnl ?? 0) - (a.pnl ?? 0);
    if (Math.abs(pnlDiff) > 0.01) return pnlDiff;
    return new Date(b.activation || 0) - new Date(a.activation || 0);
  });

  const cards = bookData.map((d, index) => {
    const money = (val, signed = false) => formatMoney(val, signed, d.currency);
    let rankBadge = '';
    if (index === 0 && d.pnl > 0) {
      rankBadge = `<span class="paper-pill" style="background:var(--mint);color:#07090d;font-weight:700">#1 MOST PROFIT</span>`;
    } else if (d.book === 'v24' || d.book === 'v30') {
      rankBadge = `<span class="paper-pill" style="background:rgba(137,155,255,0.18);color:#9daeff;border:1px solid rgba(137,155,255,0.35);font-weight:600">NEWEST INTRADAY</span>`;
    } else {
      rankBadge = `<span class="paper-pill">${(d.maximum * 100).toFixed(0)}% STATIC</span>`;
    }

    if (d.error) {
      return `<article class="ws-compare-card">
        <div class="ws-title-row"><h2>${e(d.name)}</h2>${rankBadge}</div>
        <span class="ws-value">—</span>
        <p>The saved paper ledger is unavailable or has not been activated. No balance is being assumed.</p>
        <a class="ws-btn" href="engine-book.html?book=${d.book}">Inspect ${e(d.name)} →</a>
      </article>`;
    }

    return `<article class="ws-compare-card">
      <div class="ws-title-row">
        <h2>${e(d.name)}</h2>
        ${rankBadge}
      </div>
      <span class="ws-value">${money(d.equity)}</span>
      <p class="${signClass(d.pnl)}">${money(d.pnl, true)} · ${percent(d.pnl / 100000)} since activation</p>
      <dl class="ws-trade-grid">
        <div><dt>Open positions</dt><dd>${d.openPositions}</dd></div>
        <div><dt>Closed trades</dt><dd>${d.closedTrades}</dd></div>
        <div><dt>Peak drawdown</dt><dd>${percent(d.maxDD)}</dd></div>
      </dl>
      <p class="ws-meta">${e(d.status)} · ${d.sessions} sessions<br>Ledger: ${dateLabel(d.through)}</p>
      <a class="ws-btn" href="engine-book.html?book=${d.book}">Inspect ${e(d.name)} →</a>
    </article>`;
  });

  $('comparisonCards').innerHTML = cards.join('');
  $('refreshCompare').disabled = false;
}

let legacyLoaded = false;
async function loadLegacy() {
  if (!$('legacyComparison').open || legacyLoaded) return;
  legacyLoaded = true;
  $('legacyRows').innerHTML = '<tr><td colspan="6">Loading saved ledgers…</td></tr>';
  const rows = await Promise.all(['r', 'c', 'b', 'a', 'f'].map(async book => {
    const currency = ['a', 'b', 'c'].includes(book) ? 'GBP' : 'USD';
    const title = `<a href="engine-book.html?book=${book}">Book ${book.toUpperCase()}</a>`;
    try {
      const d = await get(book), last = d.daily?.at(-1), equity = firstNumber(last?.equity);
      if (equity === null) throw new Error();
      return `<tr><td>${title}</td><td>${currency}</td><td>${formatMoney(equity, false, currency)}</td><td>${percent((equity - 100000) / 100000)}</td><td>${d.positions?.length ?? '—'}</td><td>${dateLabel(last.date)}</td></tr>`;
    } catch {
      return `<tr><td>${title}</td><td>${currency}</td><td colspan="4">Ledger unavailable</td></tr>`;
    }
  }));
  $('legacyRows').innerHTML = rows.join('');
}

$('legacyComparison').addEventListener('toggle', loadLegacy);
$('refreshCompare').addEventListener('click', () => { load(); legacyLoaded = false; loadLegacy(); });
load();
