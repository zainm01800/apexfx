/* Realistic Results JS — Clean MT4 Execution Model */

(function () {
  'use strict';

  let _positions = [];
  let _trades = [];
  let _roundTrips = [];

  function escHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function fmtPrice(val) {
    if (val == null || isNaN(val)) return '—';
    const num = Number(val);
    if (Math.abs(num) >= 1000) return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (Math.abs(num) >= 1) return num.toFixed(2);
    return num.toFixed(4);
  }

  function computeRoundTrips(trades) {
    const sorted = trades.slice().sort((a, b) => new Date(a.exec_time) - new Date(b.exec_time));
    const openInventory = {};
    const roundTrips = [];

    for (const t of sorted) {
      const sym = t.instrument;
      const qty = parseFloat(t.qty) || 0;
      const price = parseFloat(t.price) || 0;
      const side = String(t.side || '').toUpperCase();
      const isBuy = side === 'BUY';

      if (!openInventory[sym]) {
        openInventory[sym] = [];
      }

      const inv = openInventory[sym];
      let remainingQty = qty;

      while (remainingQty > 0 && inv.length > 0) {
        const head = inv[0];
        const canMatch = Math.min(remainingQty, head.qty);

        if ((isBuy && head.isBuy) || (!isBuy && !head.isBuy)) {
          break;
        }

        const buyPrice = head.isBuy ? head.price : price;
        const sellPrice = head.isBuy ? price : buyPrice;
        const direction = head.isBuy ? 'LONG' : 'SHORT';
        const pnl = direction === 'LONG'
          ? (sellPrice - buyPrice) * canMatch
          : (buyPrice - sellPrice) * canMatch;

        const costBasis = buyPrice * canMatch;
        const pnlPct = costBasis > 0 ? (pnl / costBasis) * 100 : 0;

        roundTrips.push({
          instrument: sym,
          assetClass: t.asset_class || 'stocks',
          direction: direction,
          qty: canMatch,
          entryPrice: head.price,
          exitPrice: price,
          openTime: head.exec_time,
          closeTime: t.exec_time,
          realizedPnl: pnl,
          pnlPct: pnlPct
        });

        remainingQty -= canMatch;
        head.qty -= canMatch;
        if (head.qty <= 0.00001) {
          inv.shift();
        }
      }

      if (remainingQty > 0) {
        inv.push({
          qty: remainingQty,
          price: price,
          isBuy: isBuy,
          exec_time: t.exec_time
        });
      }
    }

    return roundTrips.sort((a, b) => new Date(b.closeTime) - new Date(a.closeTime));
  }

  function loadData() {
    Promise.all([
      fetch('/api/ibkr?view=positions').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch('/api/ibkr?view=trades').then(r => r.ok ? r.json() : []).catch(() => [])
    ]).then(([pos, tr]) => {
      _positions = Array.isArray(pos) ? pos : [];
      _trades = Array.isArray(tr) ? tr : [];
      _roundTrips = computeRoundTrips(_trades);
      renderUI();
    }).catch(err => {
      console.warn('[Realistic Results] Data load error:', err);
      renderUI();
    });
  }

  function renderUI() {
    const wins = _roundTrips.filter(rt => rt.realizedPnl > 0);
    const losses = _roundTrips.filter(rt => rt.realizedPnl < 0);
    const winsSum = wins.reduce((sum, rt) => sum + rt.realizedPnl, 0);
    const lossSum = losses.reduce((sum, rt) => sum + rt.realizedPnl, 0);
    const realizedPnl = winsSum + lossSum;

    const unrealizedPnl = _positions.reduce((sum, p) => sum + (parseFloat(p.unrealized_pnl) || 0), 0);
    const cleanEquity = 1000000 + realizedPnl + unrealizedPnl;
    const cleanReturn = realizedPnl + unrealizedPnl;
    const cleanReturnPct = (cleanReturn / 1000000) * 100;
    const winRate = _roundTrips.length > 0 ? Math.round((wins.length / _roundTrips.length) * 100) : 0;

    // Update headline cards
    const eqEl = document.getElementById('realEquity');
    if (eqEl) {
      eqEl.textContent = '$' + cleanEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      eqEl.className = 'hs-val ' + (cleanReturn >= 0 ? 'green' : 'red');
    }
    const eqSub = document.getElementById('realEquitySub');
    if (eqSub) {
      eqSub.textContent = (cleanReturn >= 0 ? '+' : '') + cleanReturnPct.toFixed(2) + '% Net Profit ($' + (cleanReturn >= 0 ? '+' : '-') + Math.abs(cleanReturn).toFixed(2) + ')';
    }

    const rPnlEl = document.getElementById('realizedPnl');
    if (rPnlEl) {
      rPnlEl.textContent = (realizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(realizedPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      rPnlEl.className = 'hs-val ' + (realizedPnl >= 0 ? 'green' : 'red');
    }
    const rSub = document.getElementById('realizedSub');
    if (rSub) {
      rSub.textContent = `${wins.length} Wins · ${losses.length} Loss (${winRate}% Win Rate)`;
    }

    const uPnlEl = document.getElementById('unrealizedPnl');
    if (uPnlEl) {
      uPnlEl.textContent = (unrealizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(unrealizedPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      uPnlEl.className = 'hs-val ' + (unrealizedPnl >= 0 ? 'green' : 'red');
    }

    // Render Tables
    const container = document.getElementById('realisticTrades');
    if (!container) return;

    const head = `<div class="acc-header"><div class="acc-title">⚙️ MT5 Funded Account Trades <span class="pp-book">MetaTrader 5 Swap-Free · Zero Commission &amp; Interest</span></div></div>`;

    // Open Positions section
    const openRows = _positions.map(p => {
      const dir = String(p.direction || '').toLowerCase() === 'short' ? 'short' : 'long';
      const dirBadge = dir === 'short' ? '<span class="eng-dir short">▼ SHORT</span>' : '<span class="eng-dir long">▲ LONG</span>';
      const mv = parseFloat(p.market_value);
      const units = parseFloat(p.units);
      const entryPx = parseFloat(p.avg_price);
      const curPx = (mv && units) ? Math.abs(mv) / Math.abs(units) : entryPx;
      const upnl = parseFloat(p.unrealized_pnl);
      const pnlCls = !isNaN(upnl) ? (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : '')) : '';
      const pnlTxt = !isNaN(upnl) ? (upnl >= 0 ? '+' : '-') + '$' + Math.abs(upnl).toFixed(2) : '—';
      const mt5Ticker = p.instrument + '.US';

      return `<tr class="wl-row eng-row open">
        <td><span class="wl-sym">${escHtml(mt5Ticker)}</span><span class="wl-type">US CFD</span></td>
        <td>${dirBadge}</td>
        <td class="wl-mono">${escHtml(p.units)} lots</td>
        <td class="wl-mono">@ $${fmtPrice(entryPx)}</td>
        <td class="wl-mono">$${fmtPrice(curPx)}</td>
        <td><span class="eng-pnl ${pnlCls}">${escHtml(pnlTxt)}</span></td>
        <td class="eng-days">Live Open</td>
      </tr>`;
    }).join('');

    const openSec = `<div class="eng-sec">Open Positions · ${_positions.length}</div>` + (_positions.length
      ? `<div class="eng-wrap"><table class="wl-table eng-table">
          <thead><tr><th>Instrument (MT5)</th><th>Direction</th><th>Lots / Units</th><th>Entry Price</th><th>Current Price</th><th>Unrealized P&amp;L</th><th>Status</th></tr></thead>
          <tbody>${openRows}</tbody></table></div>`
      : `<div class="acc-empty">No open positions right now.</div>`);

    // Closed Trades section
    const closedRows = _roundTrips.map(rt => {
      const isLong = rt.direction === 'LONG';
      const dirBadge = isLong ? '<span class="eng-dir long">▲ LONG</span>' : '<span class="eng-dir short">▼ SHORT</span>';
      const isWin = rt.realizedPnl > 0;
      const ocCls = isWin ? 'win' : 'loss';
      const ocTxt = isWin ? 'WIN' : 'LOSS';
      const pnlCls = isWin ? 'pos' : 'neg';
      const pnlTxt = (rt.realizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(rt.realizedPnl).toFixed(2) + ` (${rt.pnlPct >= 0 ? '+' : ''}${rt.pnlPct.toFixed(2)}%)`;
      const closeTimeStr = rt.closeTime ? new Date(rt.closeTime).toISOString().slice(0, 16).replace('T', ' ') : '—';
      const mt5Ticker = rt.instrument + '.US';

      return `<tr class="wl-row eng-row ${ocCls}">
        <td><span class="wl-sym">${escHtml(mt5Ticker)}</span><span class="wl-type">US CFD</span></td>
        <td>${dirBadge}</td>
        <td class="wl-mono">@ $${fmtPrice(rt.entryPrice)}</td>
        <td class="wl-mono">@ $${fmtPrice(rt.exitPrice)}</td>
        <td class="wl-mono">${escHtml(closeTimeStr)}</td>
        <td><span class="eng-badge ${ocCls}">${ocTxt}</span></td>
        <td><span class="eng-pnl ${pnlCls}">${escHtml(pnlTxt)}</span></td>
        <td class="eng-days">Closed</td>
      </tr>`;
    }).join('');

    const closedSec = `<div class="eng-sec">Closed Trades &amp; Realized Round-Trips · ${_roundTrips.length}</div>` + (_roundTrips.length
      ? `<div class="eng-wrap"><table class="wl-table eng-table">
          <thead><tr><th>Instrument (MT5)</th><th>Direction</th><th>Entry Price</th><th>Exit Price</th><th>Closed Time</th><th>Outcome</th><th>Realized P&amp;L</th><th>Status</th></tr></thead>
          <tbody>${closedRows}</tbody></table></div>`
      : `<div class="acc-empty">No closed trades recorded yet.</div>`);

    container.innerHTML = head + openSec + closedSec;
  }

  document.addEventListener('DOMContentLoaded', loadData);
})();
