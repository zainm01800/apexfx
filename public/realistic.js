/* Realistic Results JS — Clean MT4 Execution Model */

(function () {
  'use strict';

  let _terminalMode = 'mt5';
  let _tradeDisplayMode = 'cards';

  window.setTerminalMode = function(mode) {
    _terminalMode = mode;
    const btnMt5 = document.getElementById('btnModeMt5');
    const btnIbkr = document.getElementById('btnModeIbkr');
    if (btnMt5) btnMt5.classList.toggle('active', mode === 'mt5');
    if (btnIbkr) btnIbkr.classList.toggle('active', mode === 'ibkr');
    renderUI();
  };

  window.setTradeDisplayMode = function(mode) {
    _tradeDisplayMode = mode;
    const btnCards = document.getElementById('btnViewCards');
    const btnTable = document.getElementById('btnViewTable');
    if (btnCards) btnCards.classList.toggle('active', mode === 'cards');
    if (btnTable) btnTable.classList.toggle('active', mode === 'table');
    renderUI();
  };

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
    const sorted = [...trades].sort((a, b) => new Date(a.exec_time) - new Date(b.exec_time));
    const openLots = {};
    const roundTrips = [];

    for (const t of sorted) {
      const inst = t.instrument;
      const price = parseFloat(t.price);
      const qty = parseFloat(t.qty);
      if (!inst || isNaN(price) || isNaN(qty) || qty <= 0) continue;

      const side = String(t.side).toUpperCase();
      let remaining = (side === 'SELL' ? -1 : 1) * qty;
      const lots = openLots[inst] || (openLots[inst] = []);

      while (remaining !== 0 && lots.length > 0 && Math.sign(lots[0].qty) !== Math.sign(remaining)) {
        const lot = lots[0];
        const closeQty = Math.min(Math.abs(remaining), Math.abs(lot.qty));
        const entryPrice = lot.price;
        const isLong = lot.qty > 0;
        const pnl = (price - entryPrice) * closeQty * (isLong ? 1 : -1);
        const pnlPct = entryPrice ? ((price - entryPrice) / entryPrice * (isLong ? 1 : -1) * 100) : 0;

        roundTrips.push({
          instrument: inst,
          assetClass: t.asset_class || 'stocks',
          direction: isLong ? 'LONG' : 'SHORT',
          qty: closeQty,
          entryPrice: entryPrice,
          exitPrice: price,
          realizedPnl: pnl,
          pnlPct: pnlPct,
          openTime: lot.exec_time,
          closeTime: t.exec_time
        });

        lot.qty -= Math.sign(lot.qty) * closeQty;
        remaining -= Math.sign(remaining) * closeQty;
        if (Math.abs(lot.qty) < 1e-12) lots.shift();
      }
      if (remaining !== 0) {
        lots.push({ qty: remaining, price, exec_time: t.exec_time, side });
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
    const isIbkr = _terminalMode === 'ibkr';
    const totalSharesTraded = _roundTrips.reduce((sum, rt) => sum + (parseFloat(rt.qty) || 0), 0) + _positions.reduce((sum, p) => sum + (parseFloat(p.units) || 0), 0);
    // MT5 Prop Firm Equity CFD Bid-Ask Spread overhead (~$0.015 / share) & Commission (~$0.01 / share)
    const spreadOverhead = isIbkr ? 0 : totalSharesTraded * 0.015;
    const propCommissions = isIbkr ? 0 : totalSharesTraded * 0.01;
    const totalOverhead = spreadOverhead + propCommissions;

    const wins = _roundTrips.filter(rt => rt.realizedPnl > 0);
    const losses = _roundTrips.filter(rt => rt.realizedPnl < 0);
    const winsSum = wins.reduce((sum, rt) => sum + rt.realizedPnl, 0);
    const lossSum = losses.reduce((sum, rt) => sum + rt.realizedPnl, 0);
    const grossRealizedPnl = winsSum + lossSum;
    const netRealizedPnl = grossRealizedPnl - totalOverhead;

    const unrealizedPnl = _positions.reduce((sum, p) => sum + (parseFloat(p.unrealized_pnl) || 0), 0);
    const cleanEquity = 1000000 + netRealizedPnl + unrealizedPnl;
    const cleanReturn = netRealizedPnl + unrealizedPnl;
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
      rPnlEl.textContent = (netRealizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(netRealizedPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      rPnlEl.className = 'hs-val ' + (netRealizedPnl >= 0 ? 'green' : 'red');
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

    const spreadEl = document.getElementById('spreadVal');
    if (spreadEl) {
      spreadEl.textContent = (spreadOverhead > 0 ? '-' : '') + '$' + Math.abs(spreadOverhead).toFixed(2);
    }

    const commEl = document.getElementById('commissionsVal');
    if (commEl) {
      commEl.textContent = (propCommissions > 0 ? '-' : '') + '$' + Math.abs(propCommissions).toFixed(2);
    }

    const bClosed = document.getElementById('bannerClosedVal');
    if (bClosed) bClosed.textContent = (netRealizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(netRealizedPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    
    const bSpread = document.getElementById('bannerSpreadVal');
    if (bSpread) bSpread.textContent = (totalOverhead > 0 ? '-' : '') + '$' + Math.abs(totalOverhead).toFixed(2);

    const bEq = document.getElementById('bannerEquityVal');
    if (bEq) bEq.textContent = '$' + cleanEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' (' + (cleanReturnPct >= 0 ? '+' : '') + cleanReturnPct.toFixed(2) + '%)';

    if (_tradeDisplayMode === 'cards') {
      renderCardsView(container, isIbkr, tickerSuffix, tickerType);
    } else {
      renderTableView(container, isIbkr, tickerSuffix, tickerType, headTitle, headSub);
    }
  }

  function renderCardsView(container, isIbkr, tickerSuffix, tickerType) {
    const allItems = [];

    // Open positions
    _positions.forEach((p, idx) => {
      const dir = String(p.direction || '').toLowerCase() === 'short' ? 'SHORT' : 'LONG';
      const mv = parseFloat(p.market_value);
      const units = parseFloat(p.units);
      const entryPx = parseFloat(p.avg_price);
      const curPx = (mv && units) ? Math.abs(mv) / Math.abs(units) : entryPx;
      const upnl = parseFloat(p.unrealized_pnl) || 0;

      allItems.push({
        id: 'open-' + idx,
        symbol: p.instrument + tickerSuffix,
        rawSymbol: p.instrument,
        direction: dir,
        isOpen: true,
        entryPrice: entryPx,
        currentPrice: curPx,
        exitPrice: curPx,
        realizedPnl: upnl,
        pnlPct: entryPx ? ((curPx - entryPx) / entryPx * (dir === 'LONG' ? 1 : -1) * 100) : 0,
        units: units,
        closeTimeStr: 'Live Open Position'
      });
    });

    // Closed round-trips
    _roundTrips.forEach((rt, idx) => {
      allItems.push({
        id: 'closed-' + idx,
        symbol: rt.instrument + tickerSuffix,
        rawSymbol: rt.instrument,
        direction: rt.direction,
        isOpen: false,
        entryPrice: rt.entryPrice,
        exitPrice: rt.exitPrice,
        currentPrice: rt.exitPrice,
        realizedPnl: rt.realizedPnl,
        pnlPct: rt.pnlPct,
        units: rt.qty,
        closeTimeStr: rt.closeTime ? new Date(rt.closeTime).toISOString().slice(0, 16).replace('T', ' ') : '—'
      });
    });

    const cardsHtml = allItems.map(item => {
      const isLong = item.direction === 'LONG';
      const dirBadge = isLong ? '<span class="eng-dir long">▲ LONG</span>' : '<span class="eng-dir short">▼ SHORT</span>';
      const isWin = item.isOpen ? item.realizedPnl >= 0 : item.realizedPnl > 0;
      const ocCls = item.isOpen ? 'open' : (isWin ? 'win' : 'loss');
      const ocTxt = item.isOpen ? 'OPEN' : (isWin ? 'WIN' : 'LOSS');
      const pnlCls = item.realizedPnl >= 0 ? 'pos' : 'neg';
      const pnlTxt = (item.realizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(item.realizedPnl).toFixed(2) + ` (${item.pnlPct >= 0 ? '+' : ''}${item.pnlPct.toFixed(2)}%)`;

      return `<div class="trade-card">
        <div class="tc-head">
          <div>
            <span class="tc-sym">${escHtml(item.symbol)}</span>
            <span class="tc-type">${tickerType}</span>
          </div>
          <div class="tc-badges">
            ${dirBadge}
            <span class="tc-badge ${ocCls}">${ocTxt}</span>
          </div>
        </div>
        <div class="tc-stats-row">
          <div class="tc-stat"><span class="tc-k">Entry</span><span class="tc-v">$${fmtPrice(item.entryPrice)}</span></div>
          <div class="tc-stat"><span class="tc-k">${item.isOpen ? 'Current' : 'Exit'}</span><span class="tc-v">$${fmtPrice(item.exitPrice)}</span></div>
          <div class="tc-stat"><span class="tc-k">P&amp;L</span><span class="tc-v ${pnlCls}">${escHtml(pnlTxt)}</span></div>
        </div>
        <div class="tc-chart-box" id="chartBox-${item.id}"></div>
        <div class="tc-foot">
          <span>${escHtml(item.units)} ${isIbkr ? 'units' : 'lots'}</span>
          <span>${escHtml(item.closeTimeStr)}</span>
        </div>
      </div>`;
    }).join('');

    container.innerHTML = `<div class="trade-card-grid">${cardsHtml}</div>`;

    // Initialize Lightweight Charts for each card
    setTimeout(() => {
      allItems.forEach(item => {
        const box = document.getElementById(`chartBox-${item.id}`);
        if (!box || !window.LightweightCharts) return;

        const isWin = item.realizedPnl >= 0;
        const color = isWin ? '#34D399' : '#F87171';
        const topColor = isWin ? 'rgba(52, 211, 153, 0.25)' : 'rgba(248, 113, 113, 0.25)';

        const chart = window.LightweightCharts.createChart(box, {
          width: box.clientWidth,
          height: 160,
          layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#64748B',
            fontFamily: "'Space Mono', monospace",
            fontSize: 10,
          },
          grid: {
            vertLines: { color: 'rgba(51, 65, 85, 0.2)' },
            horzLines: { color: 'rgba(51, 65, 85, 0.2)' },
          },
          rightPriceScale: { borderColor: 'rgba(51, 65, 85, 0.4)' },
          timeScale: { borderColor: 'rgba(51, 65, 85, 0.4)', timeVisible: false },
          crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
        });

        const series = chart.addAreaSeries({
          lineColor: color,
          lineWidth: 2,
          topColor: topColor,
          bottomColor: 'rgba(0, 0, 0, 0)',
        });

        // Generate synthetic price points reflecting entry to exit trajectory
        const startPx = item.entryPrice;
        const endPx = item.exitPrice;
        const pts = [];
        const baseTime = Math.floor(Date.now() / 1000) - 86400 * 10;

        for (let i = 0; i <= 10; i++) {
          const t = baseTime + i * 86400;
          const ratio = i / 10;
          const noise = (Math.random() - 0.5) * (Math.abs(endPx - startPx) * 0.2 || 1.5);
          const val = i === 0 ? startPx : (i === 10 ? endPx : startPx + (endPx - startPx) * ratio + noise);
          pts.push({ time: t, value: val });
        }

        series.setData(pts);
      });
    }, 50);
  }

  function renderTableView(container, isIbkr, tickerSuffix, tickerType, headTitle, headSub) {
    const head = `<div class="acc-header"><div class="acc-title">${headTitle} <span class="pp-book">${headSub}</span></div></div>`;

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
      const displaySym = p.instrument + tickerSuffix;

      return `<tr class="wl-row eng-row open">
        <td><span class="wl-sym">${escHtml(displaySym)}</span><span class="wl-type">${tickerType}</span></td>
        <td>${dirBadge}</td>
        <td class="wl-mono">${escHtml(p.units)} ${isIbkr ? 'units' : 'lots'}</td>
        <td class="wl-mono">@ $${fmtPrice(entryPx)}</td>
        <td class="wl-mono">$${fmtPrice(curPx)}</td>
        <td><span class="eng-pnl ${pnlCls}">${escHtml(pnlTxt)}</span></td>
        <td class="eng-days">Live Open</td>
      </tr>`;
    }).join('');

    const openSec = `<div class="eng-sec">Open Positions · ${_positions.length}</div>` + (_positions.length
      ? `<div class="eng-wrap"><table class="wl-table eng-table">
          <thead><tr><th>Instrument</th><th>Direction</th><th>Units / Lots</th><th>Entry Price</th><th>Current Price</th><th>Unrealized P&amp;L</th><th>Status</th></tr></thead>
          <tbody>${openRows}</tbody></table></div>`
      : `<div class="acc-empty">No open positions right now.</div>`);

    const closedRows = _roundTrips.map(rt => {
      const isLong = rt.direction === 'LONG';
      const dirBadge = isLong ? '<span class="eng-dir long">▲ LONG</span>' : '<span class="eng-dir short">▼ SHORT</span>';
      const isWin = rt.realizedPnl > 0;
      const ocCls = isWin ? 'win' : 'loss';
      const ocTxt = isWin ? 'WIN' : 'LOSS';
      const pnlCls = isWin ? 'pos' : 'neg';
      const pnlTxt = (rt.realizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(rt.realizedPnl).toFixed(2) + ` (${rt.pnlPct >= 0 ? '+' : ''}${rt.pnlPct.toFixed(2)}%)`;
      const closeTimeStr = rt.closeTime ? new Date(rt.closeTime).toISOString().slice(0, 16).replace('T', ' ') : '—';
      const displaySym = rt.instrument + tickerSuffix;

      return `<tr class="wl-row eng-row ${ocCls}">
        <td><span class="wl-sym">${escHtml(displaySym)}</span><span class="wl-type">${tickerType}</span></td>
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
          <thead><tr><th>Instrument</th><th>Direction</th><th>Entry Price</th><th>Exit Price</th><th>Closed Time</th><th>Outcome</th><th>Realized P&amp;L</th><th>Status</th></tr></thead>
          <tbody>${closedRows}</tbody></table></div>`
      : `<div class="acc-empty">No closed trades recorded yet.</div>`);

    container.innerHTML = head + openSec + closedSec;
  }

  document.addEventListener('DOMContentLoaded', loadData);
})();
