// mt4-trades.js — Client-side live broker execution monitor

let _mt4TradesFilter = 'open'; // 'open' or 'closed'
let _mt4TradesCache = [];

document.addEventListener('DOMContentLoaded', () => {
  initPulse();
  initMt4Tabs();
  
  // Start polling MT4 trades
  loadMt4Trades();
  setInterval(loadMt4Trades, 3000); // 3-second rapid refresh for live broker feed
});

function initMt4Tabs() {
  const btnOpen = document.getElementById('btnOpen');
  const btnClosed = document.getElementById('btnClosed');
  if (!btnOpen || !btnClosed) return;

  btnOpen.addEventListener('click', () => {
    btnOpen.classList.add('active');
    btnClosed.classList.remove('active');
    _mt4TradesFilter = 'open';
    renderMt4Trades();
  });

  btnClosed.addEventListener('click', () => {
    btnClosed.classList.add('active');
    btnOpen.classList.remove('active');
    _mt4TradesFilter = 'closed';
    renderMt4Trades();
  });
}

async function loadMt4Trades() {
  try {
    const res = await fetch('/api/mt4-trades');
    if (!res.ok) throw new Error('Failed to load MT4 execution data');
    _mt4TradesCache = await res.json();
    
    // Update stats scoreboard
    updateScoreboard();
    
    // Render grid
    renderMt4Trades();
  } catch (e) {
    console.error('Error fetching MT4 trades:', e);
    const grid = document.getElementById('mt4TradesGrid');
    if (grid) {
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--red); font-size: 14px;">Error syncing with MT4 bridge: ${e.message}</div>`;
    }
  }
}

function updateScoreboard() {
  const activeTrades = _mt4TradesCache.filter(t => t.status === 'open');
  const closedTrades = _mt4TradesCache.filter(t => t.status === 'closed');
  
  // 1. Active & Closed Counts
  document.getElementById('statActiveCount').textContent = activeTrades.length;
  document.getElementById('statClosedCount').textContent = closedTrades.length;
  
  // 2. Win Rate (Closed Trades only)
  if (closedTrades.length > 0) {
    const wins = closedTrades.filter(t => (t.profit || 0) > 0).length;
    const wr = (wins / closedTrades.length) * 100;
    document.getElementById('statWinRate').textContent = wr.toFixed(1) + '%';
  } else {
    document.getElementById('statWinRate').textContent = '0.0%';
  }
  
  // 3. Profit / Loss
  const totalRealised = closedTrades.reduce((acc, t) => acc + (t.profit || 0), 0);
  const totalFloating = activeTrades.reduce((acc, t) => acc + (t.profit || 0), 0);
  
  const totalProfitEl = document.getElementById('statTotalProfit');
  if (totalProfitEl) {
    const sign = totalRealised >= 0 ? '+' : '';
    const colorClass = totalRealised > 0 ? 'green' : (totalRealised < 0 ? 'red' : '');
    totalProfitEl.className = `hs-val ${colorClass}`;
    totalProfitEl.innerHTML = `${sign}£${totalRealised.toFixed(2)} <span style="font-size: 11px; font-weight: normal; color: var(--text3); display: block; margin-top: 2px;">Float: £${totalFloating.toFixed(2)}</span>`;
  }
}

function renderMt4Trades() {
  const grid = document.getElementById('mt4TradesGrid');
  if (!grid) return;

  const filtered = _mt4TradesCache.filter(t => t.status === _mt4TradesFilter);

  if (!filtered.length) {
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${_mt4TradesFilter} positions synced on MT4 terminal.</div>`;
    return;
  }

  grid.innerHTML = filtered.map(t => {
    const isBuy = t.cmd === 0;
    const sideLabel = isBuy ? 'BUY' : 'SELL';
    const sideClass = isBuy ? 'pos' : 'neg';
    
    const pnl = parseFloat(t.profit) || 0;
    const pnlClass = pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '');
    const pnlPrefix = pnl > 0 ? '+' : '';
    
    const formattedOpenTime = new Date(t.open_time * 1000).toLocaleString();
    const formattedCloseTime = t.close_time ? new Date(t.close_time * 1000).toLocaleString() : '';

    return `
      <div class="stat-item" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${t.symbol}</strong>
          <span style="font-size: 11px; font-weight: 700; color: var(--text3); font-family: var(--mono);">#${t.ticket}</span>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-top: 4px;">
          <span style="color: var(--text3)">Direction</span>
          <span class="${sideClass}" style="font-weight: 700; font-family: var(--mono);">${sideLabel} (${t.volume} Lots)</span>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Entry Price</span>
          <span style="font-family: var(--mono); color: var(--text2);">${t.open_price.toFixed(5)}</span>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Stop Loss</span>
          <span style="font-family: var(--mono); color: var(--red);">${t.sl > 0 ? t.sl.toFixed(5) : 'None'}</span>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
          <span style="color: var(--text3)">Take Profit</span>
          <span style="font-family: var(--mono); color: var(--green);">${t.tp > 0 ? t.tp.toFixed(5) : 'None'}</span>
        </div>

        ${_mt4TradesFilter === 'closed' ? `
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Close Price</span>
          <span style="font-family: var(--mono); color: var(--text2);">${t.close_price ? t.close_price.toFixed(5) : '—'}</span>
        </div>
        ` : ''}
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
          <span style="color: var(--text)">Profit / Loss</span>
          <span class="${pnlClass}" style="font-family: var(--mono); font-size: 16px;">${pnlPrefix}£${pnl.toFixed(2)}</span>
        </div>

        <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
          ${_mt4TradesFilter === 'closed' ? `Closed: ${formattedCloseTime}` : `Opened: ${formattedOpenTime}`}
        </div>
      </div>
    `;
  }).join('');
}


// ── Market Pulse Header ──────────────────────────────────────────────────────
async function loadPulse(sym, type, elId) {
  const elements = document.getElementsByClassName(elId);
  if (!elements.length) return;

  const cached = localStorage.getItem('pulse_cache_' + sym);
  if (cached) {
    try {
      const data = JSON.parse(cached);
      for (let el of elements) {
        el.querySelector('.pulse-price').textContent = data.price;
        const ce = el.querySelector('.pulse-change');
        ce.textContent = data.change;
        ce.className = `pulse-change ${data.isUp ? 'up' : 'down'}`;
      }
    } catch {}
  }

  try {
    const to = Math.floor(Date.now() / 1000);
    const from = to - 7 * 86400;
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return;
    const bars = await r.json();
    if (!Array.isArray(bars) || bars.length < 2) return;

    const curr = bars[bars.length - 1].close, prev = bars[bars.length - 2].close;
    const pct = (curr - prev) / prev * 100;
    
    const formattedPrice = type === 'Forex' ? curr.toFixed(5) : curr >= 100 ? curr.toFixed(2) : curr.toFixed(4);
    const formattedChange = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    const isUp = pct >= 0;

    localStorage.setItem('pulse_cache_' + sym, JSON.stringify({
      price: formattedPrice,
      change: formattedChange,
      isUp: isUp
    }));

    for (let el of elements) {
      el.querySelector('.pulse-price').textContent = formattedPrice;
      const ce = el.querySelector('.pulse-change');
      ce.textContent = formattedChange;
      ce.className = `pulse-change ${isUp ? 'up' : 'down'}`;
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
