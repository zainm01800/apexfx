// mt4-trades.js — Client-side live broker execution monitor

let _mt4TradesFilter = 'open'; // 'open' or 'closed'
let _mt4TradesCache = [];

let _pollIntervalId = null;

function startPolling(ms) {
  if (_pollIntervalId) clearInterval(_pollIntervalId);
  _pollIntervalId = setInterval(() => {
    try { loadMt4Trades(); } catch(e) { console.error('Poll refresh error:', e); }
  }, ms);
}

document.addEventListener('DOMContentLoaded', () => {
  try { initPulse(); } catch(e) { console.error('Pulse err:', e); }
  try { initMt4Tabs(); } catch(e) { console.error('Tabs err:', e); }
  try { initRefreshButton(); } catch(e) { console.error('Refresh btn err:', e); }
  
  // Start polling MT4 trades (initial load + slow 15-minute background auto-refresh)
  try { loadMt4Trades(); } catch(e) { console.error('Initial load err:', e); }
  startPolling(900000); // 15 minutes background refresh
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

function initRefreshButton() {
  const btnRefresh = document.getElementById('btnRefresh');
  if (btnRefresh) {
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
        await loadMt4Trades();
      } catch (e) {
        console.error('Refresh fetch error:', e);
      } finally {
        setTimeout(() => {
          btnRefresh.disabled = false;
          btnRefresh.style.opacity = '1';
          if (text) text.textContent = 'Refresh Terminal';
        }, 600);
      }
    });
  }
}

let _mt4AccountCache = {};

async function loadMt4Trades() {
  try {
    const [tradesRes, accountRes] = await Promise.all([
      fetch('/api/mt4-trades'),
      fetch('/api/mt4-account').catch(() => null)
    ]);
    
    if (!tradesRes.ok) throw new Error('Failed to load MT4 execution data');
    _mt4TradesCache = await tradesRes.json();
    
    if (accountRes && accountRes.ok) {
      _mt4AccountCache = await accountRes.json();
    }
    
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
  
  // 1. Account Details from Cache
  const startBal = _mt4AccountCache.start_balance || 10000.00;
  const currentBal = _mt4AccountCache.balance || startBal;
  const equity = _mt4AccountCache.equity || currentBal;
  const currency = _mt4AccountCache.currency || 'GBP';
  
  const symbolMap = { 'GBP': '£', 'USD': '$', 'EUR': '€', 'CHF': 'CHF' };
  const curSymbol = symbolMap[currency] || '£';
  
  document.getElementById('statStartBalance').textContent = curSymbol + startBal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.getElementById('statCurrentBalance').textContent = curSymbol + currentBal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  
  // Equity card color coding
  const equityEl = document.getElementById('statEquity');
  if (equityEl) {
    const floatDiff = equity - currentBal;
    const colorClass = floatDiff > 0 ? 'green' : (floatDiff < 0 ? 'red' : '');
    equityEl.className = `hs-val ${colorClass}`;
    equityEl.textContent = curSymbol + equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // 2. Win Rate (Closed Trades only) and Projected Win Rate (blended with current open trades)
  let realisedWins = 0;
  let realisedWR = 0;
  if (closedTrades.length > 0) {
    realisedWins = closedTrades.filter(t => (t.profit || 0) > 0).length;
    realisedWR = (realisedWins / closedTrades.length) * 100;
    document.getElementById('statWinRate').textContent = realisedWR.toFixed(1) + '%';
  } else {
    document.getElementById('statWinRate').textContent = '0.0%';
  }

  const openWins = activeTrades.filter(t => (t.profit || 0) > 0).length;
  const totalWins = realisedWins + openWins;
  const totalTrades = closedTrades.length + activeTrades.length;
  const projEl = document.getElementById('statProjectedWinRate');
  if (projEl) {
    if (totalTrades > 0) {
      const projWR = (totalWins / totalTrades) * 100;
      projEl.textContent = `Proj: ${projWR.toFixed(1)}%`;
      if (activeTrades.length > 0) {
        if (projWR > realisedWR) {
          projEl.style.color = 'var(--green)';
        } else if (projWR < realisedWR) {
          projEl.style.color = 'var(--red)';
        } else {
          projEl.style.color = 'var(--text3)';
        }
        projEl.title = `Includes ${activeTrades.length} open positions (${openWins} floating in profit).`;
      } else {
        projEl.style.color = 'var(--text3)';
        projEl.title = 'No active open positions to project.';
      }
    } else {
      projEl.textContent = 'Proj: 0.0%';
      projEl.style.color = 'var(--text3)';
    }
  }
  
  // 3. Profit / Loss
  const totalRealised = closedTrades.reduce((acc, t) => acc + (t.profit || 0), 0);
  const totalFloating = activeTrades.reduce((acc, t) => acc + (t.profit || 0), 0);
  
  const totalProfitEl = document.getElementById('statTotalProfit');
  if (totalProfitEl) {
    const sign = totalRealised >= 0 ? '+' : '';
    const colorClass = totalRealised > 0 ? 'green' : (totalRealised < 0 ? 'red' : '');
    totalProfitEl.className = `hs-val ${colorClass}`;
    
    // Bold, larger, color-coded floating P&L text
    const floatColor = totalFloating > 0 ? 'var(--green)' : (totalFloating < 0 ? 'var(--red)' : 'var(--text3)');
    const floatSign = totalFloating >= 0 ? '+' : '';
    totalProfitEl.innerHTML = `${sign}${curSymbol}${totalRealised.toFixed(2)} <span style="font-size: 13px; font-weight: 700; color: ${floatColor}; display: block; margin-top: 4px; font-family: var(--mono);">Float: ${floatSign}${curSymbol}${totalFloating.toFixed(2)}</span>`;
  }

  // 4. Average Reward:Risk (R:R)
  let rrSum = 0;
  let rrCount = 0;
  for (let t of closedTrades) {
    const risk = Math.abs(t.open_price - t.sl);
    const reward = Math.abs(t.tp - t.open_price);
    if (risk > 0 && reward > 0 && t.sl > 0 && t.tp > 0) {
      rrSum += (reward / risk);
      rrCount++;
    }
  }
  const avgRR = rrCount > 0 ? (rrSum / rrCount).toFixed(2) : '1.20';
  document.getElementById('statAverageRR').textContent = '1:' + avgRR;

  // 5. Last Sync Label
  const label = document.getElementById('lastUpdatedLabel');
  if (label) {
    if (_mt4AccountCache.updated_at) {
      const lastUpdate = new Date(_mt4AccountCache.updated_at);
      if (!isNaN(lastUpdate.getTime())) {
        const timeStr = lastUpdate.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        label.textContent = 'Last Sync: ' + timeStr;
      }
    } else {
      label.textContent = 'Last Sync: —';
    }
  }
}

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '—';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  
  const parts = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (mins > 0 || parts.length === 0) parts.push(`${mins}m`);
  return parts.join(' ');
}

window.toggleMt4Batch = function(batchId, headerId) {
  const content = document.getElementById(batchId);
  const header = document.getElementById(headerId);
  if (!content || !header) return;

  const isCollapsed = content.style.display === 'none';
  content.style.display = isCollapsed ? 'grid' : 'none';
  
  const arrow = header.querySelector('.batch-arrow');
  if (arrow) {
    arrow.textContent = isCollapsed ? '▼' : '▶';
  }
};

function renderMt4Trades() {
  const grid = document.getElementById('mt4TradesGrid');
  if (!grid) return;

  const filtered = _mt4TradesCache.filter(t => t.status === _mt4TradesFilter);

  if (!filtered.length) {
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${_mt4TradesFilter} positions synced on MT4 terminal.</div>`;
    return;
  }

  function renderTradeCard(t) {
    const isBuy = t.cmd === 0;
    const sideLabel = isBuy ? 'BUY' : 'SELL';
    const sideClass = isBuy ? 'pos' : 'neg';
    
    const pnl = parseFloat(t.profit) || 0;
    const pnlClass = pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '');
    const pnlPrefix = pnl > 0 ? '+' : '';
    
    const displaySymbol = (t.symbol || '').replace(/-g|\.m|\.ecn/gi, '').toUpperCase();
    const formattedSymbol = displaySymbol.length === 6 ? `${displaySymbol.substring(0, 3)}/${displaySymbol.substring(3)}` : displaySymbol;

    const formattedOpenTime = new Date(t.open_time * 1000).toLocaleString();
    const formattedCloseTime = t.close_time ? new Date(t.close_time * 1000).toLocaleString() : '';

    const risk = Math.abs(t.open_price - t.sl);
    const reward = Math.abs(t.tp - t.open_price);
    const rrRatio = (risk > 0 && reward > 0 && t.sl > 0 && t.tp > 0) ? (reward / risk).toFixed(2) : null;
    const rrText = rrRatio ? `1:${rrRatio}` : 'None';

    return `
      <div class="stat-item" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${formattedSymbol}</strong>
            <span class="badge-style style-${t.style || 'swing'}" style="font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; text-transform: uppercase;">${t.style || 'swing'}</span>
          </div>
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
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Take Profit</span>
          <span style="font-family: var(--mono); color: var(--green);">${t.tp > 0 ? t.tp.toFixed(5) : 'None'}</span>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Target R:R</span>
          <span style="font-family: var(--mono); color: ${rrRatio ? 'var(--accent)' : 'var(--text3)'}; font-weight: ${rrRatio ? '700' : 'normal'};">${rrText}</span>
        </div>

        ${_mt4TradesFilter === 'open' ? `
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
          <span style="color: var(--text3)">Current Price</span>
          <span style="font-family: var(--mono); color: var(--text2); font-weight: 600;">${t.close_price ? t.close_price.toFixed(5) : '—'}</span>
        </div>
        ` : `
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Close Price</span>
          <span style="font-family: var(--mono); color: var(--text2);">${t.close_price ? t.close_price.toFixed(5) : '—'}</span>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
          <span style="color: var(--text3)">Duration</span>
          <span style="font-family: var(--mono); color: var(--text2);">${formatDuration(t.close_time - t.open_time)}</span>
        </div>
        `}
        
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
          <span style="color: var(--text)">Profit / Loss</span>
          <span class="${pnlClass}" style="font-family: var(--mono); font-size: 16px;">${pnlPrefix}£${pnl.toFixed(2)}</span>
        </div>

        <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
          ${_mt4TradesFilter === 'closed' ? `Closed: ${formattedCloseTime}` : `Opened: ${formattedOpenTime}`}
        </div>
      </div>
    `;
  }

  if (_mt4TradesFilter === 'open') {
    grid.innerHTML = filtered.map(renderTradeCard).join('');
    return;
  }

  // Batch closed history (batches of 10) sorted by closed time descending (newest first)
  const sorted = [...filtered].sort((a, b) => b.close_time - a.close_time);
  const batches = [];
  const chunkSize = 10;
  for (let i = 0; i < sorted.length; i += chunkSize) {
    batches.push(sorted.slice(i, i + chunkSize));
  }

  grid.innerHTML = batches.map((chunk, index) => {
    const batchNum = batches.length - index;
    const totalPnL = chunk.reduce((s, t) => s + (parseFloat(t.profit) || 0), 0);
    const pnlClass = totalPnL > 0 ? 'pos' : (totalPnL < 0 ? 'neg' : '');
    const pnlSign = totalPnL > 0 ? '+' : '';
    
    // Win Rate
    const wins = chunk.filter(t => (parseFloat(t.profit) || 0) > 0).length;
    const winRate = chunk.length > 0 ? (wins / chunk.length * 100).toFixed(1) : '0.0';

    // Avg target R:R
    let rrSum = 0;
    let rrCount = 0;
    for (let t of chunk) {
      const risk = Math.abs(t.open_price - t.sl);
      const reward = Math.abs(t.tp - t.open_price);
      if (risk > 0 && reward > 0 && t.sl > 0 && t.tp > 0) {
        rrSum += (reward / risk);
        rrCount++;
      }
    }
    const avgRR = rrCount > 0 ? '1:' + (rrSum / rrCount).toFixed(2) : '1:1.20';

    const startIdx = index * chunkSize + 1;
    const endIdx = Math.min((index + 1) * chunkSize, sorted.length);

    // Default first (most recent) batch to open, others closed
    const isExpanded = index === 0;
    const displayStyle = isExpanded ? 'grid' : 'none';
    const arrowSymbol = isExpanded ? '▼' : '▶';

    const batchId = `mt4Batch_${index}`;
    const headerId = `mt4BatchHeader_${index}`;

    return `
      <div style="grid-column: 1 / -1; display: flex; flex-direction: column; gap: 8px;">
        <div id="${headerId}" onclick="toggleMt4Batch('${batchId}', '${headerId}')" style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: background 0.2s, border-color 0.2s; user-select: none;" onmouseover="this.style.background='rgba(0, 240, 255, 0.04)'; this.style.borderColor='rgba(0, 240, 255, 0.2)';" onmouseout="this.style.background='rgba(255, 255, 255, 0.02)'; this.style.borderColor='var(--border)';">
          <div style="display: flex; align-items: center; gap: 12px;">
            <span class="batch-arrow" style="font-size: 11px; color: var(--accent); font-family: var(--mono);">${arrowSymbol}</span>
            <strong style="font-size: 15px; color: var(--text);">Batch ${batchNum} <span style="font-size: 12px; font-weight: normal; color: var(--text3); font-family: var(--mono); margin-left: 6px;">(Trades ${startIdx} - ${endIdx} of ${sorted.length})</span></strong>
          </div>
          
          <div style="display: flex; align-items: center; gap: 24px; font-size: 13px; font-family: var(--mono); flex-wrap: wrap;">
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
              <span style="font-size: 9px; color: var(--text3); text-transform: uppercase;">Win Rate</span>
              <span class="${parseFloat(winRate) >= 50 ? 'pos' : 'neg'}" style="font-weight: 700;">${winRate}%</span>
            </div>
            
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
              <span style="font-size: 9px; color: var(--text3); text-transform: uppercase;">Avg R:R</span>
              <span style="color: var(--accent); font-weight: 700;">${avgRR}</span>
            </div>
            
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
              <span style="font-size: 9px; color: var(--text3); text-transform: uppercase;">P&L</span>
              <span class="${pnlClass}" style="font-weight: 700;">${pnlSign}£${totalPnL.toFixed(2)}</span>
            </div>
          </div>
        </div>
        
        <div id="${batchId}" style="display: ${displayStyle}; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px; margin-top: 6px; margin-bottom: 12px;">
          ${chunk.map(renderTradeCard).join('')}
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
