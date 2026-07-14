// mt4-trades.js — Client-side live broker execution monitor

let _mt4TradesFilter = 'open'; // 'open', 'closed', or 'lessons'
let _mt4TradesCache = [];
let _engineLessonsCache = [];

// OANDA MT4 server runs on EET (UTC+2). Timestamps stored in mt4_positions.json
// are in broker server time. We subtract the offset to display in the user's local time.
// OANDA EET offset vs UTC = +2h. If your local timezone changes (e.g. winter GMT), adjust this.
const MT4_BROKER_OFFSET_HOURS = 2; // OANDA server = EET (UTC+2)

function mt4Time(unixSeconds) {
  // Convert broker server time to UTC by subtracting the broker offset,
  // then let the browser display in the user's local timezone.
  const brokerOffsetMs = MT4_BROKER_OFFSET_HOURS * 3600 * 1000;
  return new Date(unixSeconds * 1000 - brokerOffsetMs);
}

function formatLessonText(text) {
  if (!text) return '';
  function cleanVal(val) {
    if (val === null || val === undefined) return '';
    if (typeof val === 'object') {
      if (Array.isArray(val)) {
        return val.map(cleanVal).join(' · ');
      }
      return Object.entries(val).map(([k, v]) => {
        const kClean = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return `${kClean}: ${cleanVal(v)}`;
      }).join(' · ');
    }
    return String(val).trim();
  }
  let decoded = text.replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  const jsonRegex = /(\{[^{}]+\})/g;
  let hasReplacement = false;
  let formatted = decoded.replace(jsonRegex, (match) => {
    try {
      const parsed = JSON.parse(match);
      hasReplacement = true;
      return cleanVal(parsed);
    } catch (e) {
      return match;
    }
  });
  return hasReplacement ? formatted : text;
}

window.navigateToLesson = function(ticket) {
  const btnLessons = document.getElementById('btnLessons');
  const btnOpen = document.getElementById('btnOpen');
  const btnClosed = document.getElementById('btnClosed');
  
  if (!btnLessons) return;
  
  _mt4TradesFilter = 'lessons';
  btnLessons.classList.add('active');
  if (btnOpen) btnOpen.classList.remove('active');
  if (btnClosed) btnClosed.classList.remove('active');
  
  renderMt4Trades();
  
  let attempts = 0;
  const maxAttempts = 20;
  const interval = setInterval(() => {
    attempts++;
    
    const targetCard = document.querySelector(`[data-lesson-ticket="${ticket}"]`);
    
    if (targetCard) {
      clearInterval(interval);
      
      const batchContainer = targetCard.closest('[id^="mt4LessonBatch_"]');
      if (batchContainer) {
        const batchIndex = batchContainer.id.replace('mt4LessonBatch_', '');
        const headerId = `mt4LessonBatchHeader_${batchIndex}`;
        
        if (batchContainer.style.display === 'none') {
          toggleMt4Batch(batchContainer.id, headerId);
        }
      }
      
      // Clear any other highlighted lesson card
      document.querySelectorAll('.stat-item').forEach(c => {
        c.classList.remove('highlighted-lesson');
        c.style.outline = '1px solid var(--border)';
        c.style.boxShadow = '0 4px 15px rgba(0,0,0,0.3)';
        c.style.transform = 'scale(1)';
      });
      
      targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
      
      // Apply active highlight state
      targetCard.classList.add('highlighted-lesson');
      targetCard.style.outline = '2.5px solid var(--accent)';
      targetCard.style.boxShadow = '0 0 25px rgba(0, 240, 255, 0.6)';
      targetCard.style.transform = 'scale(1.02)';
      
    } else if (attempts >= maxAttempts) {
      clearInterval(interval);
      console.warn('Lesson card not found for ticket:', ticket);
    }
  }, 150);
};

// Add global listener to clear highlight on click away
document.addEventListener('click', (e) => {
  // Only clear if we clicked outside any highlighted card
  const highlighted = document.querySelector('.highlighted-lesson');
  if (highlighted && !highlighted.contains(e.target)) {
    highlighted.classList.remove('highlighted-lesson');
    highlighted.style.transition = 'outline 0.5s, box-shadow 0.5s, transform 0.5s';
    highlighted.style.outline = '1px solid var(--border)';
    highlighted.style.boxShadow = '0 4px 15px rgba(0,0,0,0.3)';
    highlighted.style.transform = 'scale(1)';
  }
});

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
  const btnLessons = document.getElementById('btnLessons');
  if (!btnOpen || !btnClosed || !btnLessons) return;

  btnOpen.addEventListener('click', () => {
    btnOpen.classList.add('active');
    btnClosed.classList.remove('active');
    btnLessons.classList.remove('active');
    _mt4TradesFilter = 'open';
    renderMt4Trades();
  });

  btnClosed.addEventListener('click', () => {
    btnClosed.classList.add('active');
    btnOpen.classList.remove('active');
    btnLessons.classList.remove('active');
    _mt4TradesFilter = 'closed';
    renderMt4Trades();
  });

  btnLessons.addEventListener('click', () => {
    btnLessons.classList.add('active');
    btnOpen.classList.remove('active');
    btnClosed.classList.remove('active');
    _mt4TradesFilter = 'lessons';
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
    _engineLessonsCache = []; // clear to allow reload
    const [tradesRes, accountRes] = await Promise.all([
      fetch('/api/mt4-trades'),
      fetch('/api/mt4-account').catch(() => null)
    ]);
    
    if (!tradesRes.ok) throw new Error('Failed to load MT4 execution data');
    const rawTrades = await tradesRes.json();
    const testTickets = [361819242, 361819268, 361819276];
    _mt4TradesCache = rawTrades.filter(t => !testTickets.includes(t.ticket));
    
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

function getExitReason(t) {
  const isBuy = t.cmd === 0;
  const tp = parseFloat(t.tp) || 0;
  const sl = parseFloat(t.sl) || 0;
  const closePrice = parseFloat(t.close_price) || 0;
  const openPrice = parseFloat(t.open_price) || 0;
  const profit = parseFloat(t.profit) || 0;

  if (closePrice <= 0) return 'Closed';

  // Relative tolerance: 0.02% of the close price to handle spread/slippage cleanly across all asset classes
  const tolerance = closePrice * 0.0002;

  if (isBuy) {
    if (tp > 0 && closePrice >= (tp - tolerance)) {
      return 'TP Hit';
    }
    if (sl > 0 && closePrice <= (sl + tolerance)) {
      if (Math.abs(sl - openPrice) <= (openPrice * 0.0005)) {
        return 'Breakeven Stop Hit';
      }
      if (profit > 0) {
        return 'Trailing Stop Hit';
      }
      return 'SL Hit';
    }
  } else { // SELL
    if (tp > 0 && closePrice <= (tp + tolerance)) {
      return 'TP Hit';
    }
    if (sl > 0 && closePrice >= (sl - tolerance)) {
      if (Math.abs(sl - openPrice) <= (openPrice * 0.0005)) {
        return 'Breakeven Stop Hit';
      }
      if (profit > 0) {
        return 'Trailing Stop Hit';
      }
      return 'SL Hit';
    }
  }

  return 'Manually Closed';
}

function renderMt4Trades() {
  const grid = document.getElementById('mt4TradesGrid');
  if (!grid) return;

  const previouslyHighlightedTicket = document.querySelector('.highlighted-lesson')?.getAttribute('data-lesson-ticket');

  function setGridHtml(html) {
    grid.innerHTML = html;
    if (previouslyHighlightedTicket) {
      const newCard = document.querySelector(`[data-lesson-ticket="${previouslyHighlightedTicket}"]`);
      if (newCard) {
        newCard.classList.add('highlighted-lesson');
        newCard.style.outline = '2.5px solid var(--accent)';
        newCard.style.boxShadow = '0 0 25px rgba(0, 240, 255, 0.6)';
        newCard.style.transform = 'scale(1.02)';
      }
    }
  }

  const filterKey = _mt4TradesFilter === 'lessons' ? 'closed' : _mt4TradesFilter;
  const filtered = _mt4TradesCache.filter(t => t.status === filterKey);

  if (!filtered.length && _mt4TradesFilter !== 'lessons') {
    setGridHtml(`<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${_mt4TradesFilter} positions synced on MT4 terminal.</div>`);
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
    const formattedSymbol = displaySymbol.length === 6
      ? `${displaySymbol.substring(0, 3)}/${displaySymbol.substring(3)}`
      : displaySymbol;

    const formattedOpenTime  = mt4Time(t.open_time).toLocaleString();
    const formattedCloseTime = t.close_time ? mt4Time(t.close_time).toLocaleString() : '';

    const risk    = Math.abs(t.open_price - t.sl);
    const reward  = Math.abs(t.tp - t.open_price);
    const rrRatio = (risk > 0 && reward > 0 && t.sl > 0 && t.tp > 0) ? (reward / risk).toFixed(2) : null;
    const rrText  = rrRatio ? `1:${rrRatio}` : 'None';

    // ── WIN / LOSS / MANAGED badge (closed cards only) ───────────────────
    const isClosedView = _mt4TradesFilter === 'closed';
    let winBadge = '';
    let exitReasonBadge = '';
    if (isClosedView) {
      const exitReason = getExitReason(t);

      if (exitReason === 'TP Hit') {
        winBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">WIN</span>`;
        exitReasonBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.1);color:var(--green);font-family:var(--mono);border:1px solid rgba(0,200,100,0.25);letter-spacing:0.04em;">TP HIT</span>`;
      } else if (exitReason === 'SL Hit') {
        winBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">LOSS</span>`;
        exitReasonBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.1);color:var(--red);font-family:var(--mono);border:1px solid rgba(255,70,70,0.25);letter-spacing:0.04em;">SL HIT</span>`;
      } else {
        winBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,170,0,0.15);color:#ffaa00;font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,170,0,0.2);">MANAGED</span>`;
        if (exitReason === 'Trailing Stop Hit') {
          exitReasonBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,195,255,0.1);color:var(--accent);font-family:var(--mono);border:1px solid rgba(0,195,255,0.25);letter-spacing:0.04em;">TRAILING SL</span>`;
        } else if (exitReason === 'Breakeven Stop Hit') {
          exitReasonBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,170,0,0.1);color:#ffaa00;font-family:var(--mono);border:1px solid rgba(255,170,0,0.25);letter-spacing:0.04em;">BE SL HIT</span>`;
        } else {
          exitReasonBadge = `<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,255,255,0.05);color:var(--text3);font-family:var(--mono);border:1px solid var(--border);letter-spacing:0.04em;">MANUAL</span>`;
        }
      }
    }

    // ── PARTIAL CLOSE detection ───────────────────────────────────────────
    // When the EA partially closes a trade, MT4 logs it as a separate history
    // entry on the same symbol with a smaller lot size. We surface those here.
    const allClosed = _mt4TradesCache.filter(x => x.status === 'closed');
    let partials = [];
    if (_mt4TradesFilter === 'open') {
      // Open position: find history closes on same symbol after this trade opened
      partials = allClosed.filter(h => {
        const hSym = (h.symbol || '').replace(/-g|\.m|\.ecn/gi, '').toUpperCase();
        return hSym === displaySymbol && h.close_time > t.open_time && h.volume < t.volume;
      });
    } else {
      // Closed position: find history closes on same symbol during its lifetime
      partials = allClosed.filter(h => {
        const hSym = (h.symbol || '').replace(/-g|\.m|\.ecn/gi, '').toUpperCase();
        return hSym === displaySymbol
          && h.ticket !== t.ticket
          && h.close_time >= t.open_time
          && h.close_time <= t.close_time
          && h.volume < t.volume;
      });
    }

    const partialsHtml = partials.length > 0 ? `
      <div style="margin-top:4px;padding:8px 10px;border-radius:8px;background:rgba(0,240,255,0.04);border:1px solid rgba(0,240,255,0.14);">
        <div style="font-size:10px;color:var(--accent);font-weight:700;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px;">
          ⚡ Partial Closes &nbsp;<span style="color:var(--text3);font-weight:400;">(${partials.length})</span>
        </div>
        ${partials.map(p => {
          const pp  = parseFloat(p.profit) || 0;
          const pps = (pp >= 0 ? '+' : '') + '£' + pp.toFixed(2);
          const ppc = pp > 0 ? 'var(--green)' : (pp < 0 ? 'var(--red)' : 'var(--text3)');
          const cd  = new Date(p.close_time * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
          return `<div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;font-family:var(--mono);padding:2px 0;">
            <span style="color:var(--text3);">${cd} · ${p.volume} lots @ ${p.close_price ? p.close_price.toFixed(5) : '—'}</span>
            <span style="font-weight:700;color:${ppc};">${pps}</span>
          </div>`;
        }).join('')}
      </div>
    ` : '';

    return `
      <div class="stat-item" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
            <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${formattedSymbol}</strong>
            <span class="badge-style style-${t.style || 'swing'}" style="font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; text-transform: uppercase;">${t.style || 'swing'}</span>
            ${winBadge}
            ${exitReasonBadge}
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

        ${partialsHtml}

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
          <span style="color: var(--text)">Profit / Loss</span>
          <span class="${pnlClass}" style="font-family: var(--mono); font-size: 16px;">${pnlPrefix}£${pnl.toFixed(2)}</span>
        </div>

        ${_mt4TradesFilter === 'closed' ? `
        <button onclick="event.stopPropagation(); navigateToLesson(${t.ticket})" style="width: 100%; margin-top: 8px; padding: 8px 12px; background: rgba(0, 240, 255, 0.06); border: 1px solid rgba(0, 240, 255, 0.2); border-radius: 8px; color: var(--accent); font-size: 12px; font-weight: 700; font-family: var(--mono); cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; transition: background 0.2s, border-color 0.2s, transform 0.1s;" onmouseover="this.style.background='rgba(0, 240, 255, 0.16)'; this.style.borderColor='rgba(0, 240, 255, 0.4)';" onmouseout="this.style.background='rgba(0, 240, 255, 0.06)'; this.style.borderColor='rgba(0, 240, 255, 0.2)';" onmousedown="this.style.transform='scale(0.98)'" onmouseup="this.style.transform='scale(1)'">
          🧠 Show Engine Lesson
        </button>
        ` : ''}

        <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
          ${_mt4TradesFilter === 'closed' ? `Closed: ${formattedCloseTime}` : `Opened: ${formattedOpenTime}`}
        </div>
      </div>
    `;
  }


  function getCleanSymbol(sym) {
    return (sym || '').replace(/-g|\.m|\.ecn|\//gi, '').toUpperCase();
  }

  function renderLessonCardForTrade(t) {
    const isBuy = t.cmd === 0;
    const sideLabel = isBuy ? 'BUY' : 'SELL';
    const sideClass = isBuy ? 'pos' : 'neg';

    const pnl = parseFloat(t.profit) || 0;
    const pnlClass = pnl > 0 ? 'pos' : (pnl < 0 ? 'neg' : '');
    const pnlPrefix = pnl > 0 ? '+' : '';

    const displaySymbol = (t.symbol || '').replace(/-g|\.m|\.ecn/gi, '').toUpperCase();
    const formattedSymbol = displaySymbol.length === 6
      ? `${displaySymbol.substring(0, 3)}/${displaySymbol.substring(3)}`
      : displaySymbol;

    const formattedDate = t.close_time ? mt4Time(t.close_time).toLocaleDateString() : '';

    const isLoss = pnl < 0;
    const isWin = pnl > 0;

    // Match with Supabase AI post-mortem lessons if available using time proximity
    const cleanedSym = getCleanSymbol(t.symbol);
    
    function getTimestampFromSetupId(id) {
      const parts = (id || '').split('_');
      if (parts.length < 2) return 0;
      let ts = parseFloat(parts[parts.length - 1]);
      if (isNaN(ts)) return 0;
      return ts > 1000000000000 ? ts / 1000.0 : ts;
    }

    const symbolLessons = _engineLessonsCache.filter(x => getCleanSymbol(x.symbol) === cleanedSym);
    let matchedAi = null;
    if (symbolLessons.length > 0) {
      let bestScore = 9999999.0;
      const tDirection = t.cmd === 0 ? 'BUY' : 'SELL';
      const tPrice = parseFloat(t.open_price) || 0;
      const tSl = parseFloat(t.sl) || 0;
      const tTp = parseFloat(t.tp) || 0;

      for (const l of symbolLessons) {
        // 1. Direction check
        const mVerdict = (l.verdict || '').toUpperCase().trim();
        if (mVerdict !== tDirection) continue;

        // 2. Time proximity check (must be within 36 hours)
        const setupTime = getTimestampFromSetupId(l.id);
        if (setupTime <= 0) continue;
        const timeDiffHours = Math.abs(t.open_time - setupTime) / 3600.0;
        if (timeDiffHours > 36.0) continue;

        // 3. Price proximity check (must be within 150 pips)
        const mPrice = parseFloat(l.price) || 0;
        const priceDiff = Math.abs(tPrice - mPrice);
        const pipScale = tPrice > 50.0 ? 1.0 : 0.0100;
        if (priceDiff > (1.50 * pipScale)) continue;

        // Calculate total price errors
        const mSl = parseFloat(l.stop_loss) || 0;
        const slDiff = (tSl > 0 && mSl > 0) ? Math.abs(tSl - mSl) : 0.0;

        const mTp = parseFloat(l.target_price) || 0;
        const tpDiff = (tTp > 0 && mTp > 0) ? Math.abs(tTp - mTp) : 0.0;

        const totalError = priceDiff + slDiff + tpDiff;
        const score = totalError * 1000.0 + timeDiffHours;

        if (score < bestScore) {
          bestScore = score;
          matchedAi = l;
        }
      }
    }
    
    // Determine targets hit status for fallback lesson selection
    const tpValForFallback = parseFloat(t.tp) || (matchedAi ? parseFloat(matchedAi.target_price) : 0);
    const slValForFallback = parseFloat(t.sl) || (matchedAi ? parseFloat(matchedAi.stop_loss) : 0);
    const closeValForFallback = parseFloat(t.close_price) || 0;
    
    let hitTpFallback = false;
    let hitSlFallback = false;
    
    if (matchedAi && matchedAi.outcome) {
      hitTpFallback = matchedAi.outcome === 'tp_hit';
      hitSlFallback = matchedAi.outcome === 'sl_hit';
    } else {
      hitTpFallback = tpValForFallback > 0 && Math.abs(closeValForFallback - tpValForFallback) < 0.0002;
      hitSlFallback = slValForFallback > 0 && Math.abs(closeValForFallback - slValForFallback) < 0.0002;
    }

    let lessonText = '';
    let isAiLesson = false;
    if (matchedAi && matchedAi.lesson) {
      lessonText = matchedAi.lesson;
      isAiLesson = true;
    } else {
      // Dynamic fallback post-mortem lesson
      if (hitTpFallback) {
        lessonText = `<strong>✅ What Went Right:</strong> The setup reached its profit target. Trend momentum aligned correctly and execution parameters protected the locked profit.<br><strong>📊 Why It Worked:</strong> Market structure and regime conditions were favourable for the direction taken.<br><strong>🔒 What to Preserve:</strong> Maintain this entry criteria and position sizing discipline on similar setups.<br><strong>🎯 Action Plan:</strong> Continue executing the same process on setups with matching confluence.`;
      } else if (hitSlFallback) {
        lessonText = `<strong>❌ What Went Wrong:</strong> The setup was stopped out. Market structure shifted against the trade bias.<br><strong>🔍 Why It Went Wrong:</strong> The engine has recorded the regime conditions that led to this outcome.<br><strong>💡 What Can Be Improved:</strong> Review the entry confluence score and consider tighter regime filtering.<br><strong>🎯 Action Plan to Prevent Recurrence:</strong> Adjust system weighting to reduce exposure on similar high-volatility regimes.`;
      } else {
        lessonText = `<strong>🔄 What Happened:</strong> Position was closed before reaching SL or TP — managed exit or invalidation.<br><strong>📐 Why It Was Managed Out:</strong> Trade conditions changed or a manual/automated risk limit triggered the early close.<br><strong>⚖️ Was the Decision Correct?</strong> Closing early preserved capital/gains and prevented full stop loss hit — this is active defense.<br><strong>🎯 Action Plan for Similar Setups:</strong> Review what triggered the early exit and whether holding longer would have been justified.`;
      }
    }

    // Detect lesson category from stored HTML emoji marker
    // This ensures the visual style ALWAYS matches the actual lesson content,
    // even if the trade's PnL and stored category diverge.
    let lessonCat = 'loss'; // safe default
    const lessonFirst80 = lessonText.slice(0, 80);
    if (lessonFirst80.includes('✅')) lessonCat = 'win';
    else if (lessonFirst80.includes('🔄')) lessonCat = 'neutral';
    else if (lessonFirst80.includes('❌')) lessonCat = 'loss';
    else if (hitTpFallback) lessonCat = 'win';
    else if (!hitSlFallback) lessonCat = 'neutral';

    const lessonBg = lessonCat === 'win'     ? 'rgba(0, 240, 255, 0.03)'
                   : lessonCat === 'neutral' ? 'rgba(255, 165, 0, 0.05)'
                   :                           'rgba(255, 70, 70, 0.04)';
    const lessonBorder = lessonCat === 'win'     ? 'rgba(0, 240, 255, 0.12)'
                       : lessonCat === 'neutral' ? 'rgba(255, 165, 0, 0.25)'
                       :                           'rgba(255, 70, 70, 0.15)';
    const lessonHeaderColor = lessonCat === 'win'     ? 'var(--accent)'
                             : lessonCat === 'neutral' ? '#f0a832'
                             :                           'var(--red)';
    const lessonTitle = lessonCat === 'neutral' ? '🧠 Post-Mortem Review'
                       : '🧠 Post-Mortem Lesson';

    let finalLabel = 'BREAKEVEN';
    let finalColor = 'var(--text3)';
    if (lessonCat === 'win') {
      finalLabel = 'WIN';
      finalColor = 'var(--green)';
    } else if (lessonCat === 'loss') {
      finalLabel = 'LOSS';
      finalColor = 'var(--red)';
    } else if (lessonCat === 'neutral') {
      finalLabel = 'MANAGED';
      finalColor = '#ffaa00';
    }

    // Determine close reason and targets for display
    let outcomeLabel = 'Closed Manually (Managed Exit)'; // default
    let closeReasonColor = '#ffaa00'; // orange
    
    if (matchedAi && matchedAi.outcome) {
      if (matchedAi.outcome === 'tp_hit') {
        outcomeLabel = 'Hit Take Profit';
        closeReasonColor = 'var(--green)';
      } else if (matchedAi.outcome === 'sl_hit') {
        outcomeLabel = 'Hit Stop Loss';
        closeReasonColor = 'var(--red)';
      } else if (matchedAi.outcome === 'invalidated') {
        outcomeLabel = 'Closed Manually (Managed Exit)';
        closeReasonColor = '#ffaa00';
      } else if (matchedAi.outcome === 'expired') {
        outcomeLabel = 'Expired';
        closeReasonColor = 'var(--text3)';
      }
    } else {
      // fallback detection
      const tpVal = parseFloat(t.tp) || 0;
      const slVal = parseFloat(t.sl) || 0;
      const closeVal = parseFloat(t.close_price) || 0;
      if (tpVal > 0 && Math.abs(closeVal - tpVal) < 0.0002) {
        outcomeLabel = 'Hit Take Profit';
        closeReasonColor = 'var(--green)';
      } else if (slVal > 0 && Math.abs(closeVal - slVal) < 0.0002) {
        outcomeLabel = 'Hit Stop Loss';
        closeReasonColor = 'var(--red)';
      }
    }

    const displayTp = parseFloat(t.tp) || (matchedAi ? parseFloat(matchedAi.target_price) : 0);
    const displaySl = parseFloat(t.sl) || (matchedAi ? parseFloat(matchedAi.stop_loss) : 0);
    const tpText = displayTp > 0 ? displayTp.toFixed(5) : '—';
    const slText = displaySl > 0 ? displaySl.toFixed(5) : '—';

    return `
      <div class="stat-item" data-lesson-ticket="${t.ticket}" data-lesson-sym="${cleanedSym}" data-lesson-open="${t.open_time}" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s, outline 0.2s, box-shadow 0.2s;">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 8px;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${formattedSymbol}</strong>
            <span class="badge-style style-${t.style || 'swing'}" style="font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; text-transform: uppercase;">${t.style || 'swing'}</span>
          </div>
          <span style="font-size: 11px; font-weight: 700; font-family: var(--mono); color: ${finalColor};">${finalLabel}</span>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Setup Direction</span>
          <span class="${sideClass}" style="font-weight: 700; font-family: var(--mono);">${sideLabel} (${t.volume} Lots)</span>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Entry / Exit Price</span>
          <span style="font-family: var(--mono); color: var(--text2);">${t.open_price.toFixed(5)} / ${t.close_price.toFixed(5)}</span>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Target TP / SL</span>
          <span style="font-family: var(--mono); color: var(--text2);">${tpText} / ${slText}</span>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Close Reason</span>
          <span style="font-family: var(--mono); font-weight: 700; color: ${closeReasonColor};">${outcomeLabel}</span>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
          <span style="color: var(--text3)">Net P&L</span>
          <span class="${pnlClass}" style="font-family: var(--mono); font-weight: 700;">${pnlPrefix}£${pnl.toFixed(2)}</span>
        </div>

        <div style="padding: 12px; border-radius: 8px; background: ${lessonBg}; border: 1px solid ${lessonBorder}; display: flex; flex-direction: column; gap: 6px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="font-size: 10px; color: ${lessonHeaderColor}; text-transform: uppercase; letter-spacing: 0.05em; font-family: var(--mono);">${lessonTitle}</strong>
            ${isAiLesson ? `<span style="font-size: 9px; font-weight: 700; color: var(--green); background: rgba(0, 200, 100, 0.15); padding: 1px 5px; border-radius: 3px; font-family: var(--mono);">AI LEARNING</span>` : `<span style="font-size: 9px; font-weight: 700; color: var(--text3); background: rgba(255, 255, 255, 0.05); padding: 1px 5px; border-radius: 3px; font-family: var(--mono);">DYNAMIC</span>`}
          </div>
          <div style="font-size: 12.5px; color: var(--text2); margin: 0; line-height: 1.5; font-family: inherit;">
            <div>${formatLessonText(lessonText).replace(/<br>/gi, '</div><div style="margin-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.04); padding-top: 6px;">')}</div>
          </div>
        </div>

        <div style="font-size: 10.5px; color: var(--text3); text-align: right; font-style: italic; margin-top: 4px;">
          Closed: ${formattedDate}
        </div>
      </div>
    `;
  }

  if (_mt4TradesFilter === 'lessons') {
    const closedTrades = _mt4TradesCache.filter(t => t.status === 'closed');

    if (closedTrades.length === 0) {
      setGridHtml(`<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No closed trades found to generate lessons.</div>`);
      return;
    }

    if (!_engineLessonsCache || _engineLessonsCache.length === 0) {
      // Fetch in background to enrich cards
      fetch('/api/memory?all=true&resolved=true&lean=true&symbol=ilike.*%2F*&limit=1000')
        .then(r => r.json())
        .then(data => {
          _engineLessonsCache = data.filter(t => t.lesson && t.lesson.trim() !== '');
          renderMt4Trades();
        })
        .catch(err => console.error('Error fetching lessons to enrich:', err));
    }

    // Batch closed history (batches of 10) sorted by closed time ascending (oldest first)
    const sorted = [...closedTrades].sort((a, b) => a.close_time - b.close_time);
    
    const chunks = [];
    const chunkSize = 10;
    for (let i = 0; i < sorted.length; i += chunkSize) {
      chunks.push({
        batchNum: Math.floor(i / chunkSize) + 1,
        startIdx: i + 1,
        endIdx: Math.min(i + chunkSize, sorted.length),
        trades: sorted.slice(i, i + chunkSize)
      });
    }

    // Display chunks with newest batch on top
    const displayChunks = [...chunks].reverse();

    setGridHtml(displayChunks.map((chunk, index) => {
      // Within each batch, display trades descending (newest first)
      const batchTrades = [...chunk.trades].reverse();
      
      const totalPnL = batchTrades.reduce((s, t) => s + (parseFloat(t.profit) || 0), 0);
      const pnlClass = totalPnL > 0 ? 'pos' : (totalPnL < 0 ? 'neg' : '');
      const pnlSign = totalPnL > 0 ? '+' : '';
      
      // Win Rate
      const wins = batchTrades.filter(t => (parseFloat(t.profit) || 0) > 0).length;
      const winRate = batchTrades.length > 0 ? (wins / batchTrades.length * 100).toFixed(1) : '0.0';

      // Avg target R:R
      let rrSum = 0;
      let rrCount = 0;
      for (let t of batchTrades) {
        const risk = Math.abs(t.open_price - t.sl);
        const reward = Math.abs(t.tp - t.open_price);
        if (risk > 0 && reward > 0 && t.sl > 0 && t.tp > 0) {
          rrSum += (reward / risk);
          rrCount++;
        }
      }
      const avgRR = rrCount > 0 ? '1:' + (rrSum / rrCount).toFixed(2) : '1:1.20';

      const batchId = `mt4LessonBatch_${index}`;
      const headerId = `mt4LessonBatchHeader_${index}`;

      const existingBatch = document.getElementById(batchId);
      const displayStyle = (existingBatch && existingBatch.style.display !== 'none') ? 'grid' : 'none';
      const arrowSymbol = (existingBatch && existingBatch.style.display !== 'none') ? '▼' : '▶';

      return `
        <div style="grid-column: 1 / -1; display: flex; flex-direction: column; gap: 8px;">
          <div id="${headerId}" onclick="toggleMt4Batch('${batchId}', '${headerId}')" style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: background 0.2s, border-color 0.2s; user-select: none;" onmouseover="this.style.background='rgba(0, 240, 255, 0.04)'; this.style.borderColor='rgba(0, 240, 255, 0.2)';" onmouseout="this.style.background='rgba(255, 255, 255, 0.02)'; this.style.borderColor='var(--border)';">
            <div style="display: flex; align-items: center; gap: 12px;">
              <span class="batch-arrow" style="font-size: 11px; color: var(--accent); font-family: var(--mono);">${arrowSymbol}</span>
              <strong style="font-size: 15px; color: var(--text);">Lesson Batch ${chunk.batchNum} <span style="font-size: 12px; font-weight: normal; color: var(--text3); font-family: var(--mono); margin-left: 6px;">(Trades ${chunk.startIdx} - ${chunk.endIdx} of ${sorted.length})</span></strong>
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
            ${batchTrades.map(renderLessonCardForTrade).join('')}
          </div>
        </div>
      `;
    }).join(''));
    return;
  }

  if (_mt4TradesFilter === 'open') {
    setGridHtml(filtered.map(renderTradeCard).join(''));
    return;
  }

  // Batch closed history (batches of 10) sorted by closed time ascending (oldest first)
  const sorted = [...filtered].sort((a, b) => a.close_time - b.close_time);
  
  const chunks = [];
  const chunkSize = 10;
  for (let i = 0; i < sorted.length; i += chunkSize) {
    chunks.push({
      batchNum: Math.floor(i / chunkSize) + 1,
      startIdx: i + 1,
      endIdx: Math.min(i + chunkSize, sorted.length),
      trades: sorted.slice(i, i + chunkSize)
    });
  }

  // Display chunks with newest batch on top
  const displayChunks = [...chunks].reverse();

  setGridHtml(displayChunks.map((chunk, index) => {
    // Within each batch, display trades descending (newest first)
    const batchTrades = [...chunk.trades].reverse();
    
    const totalPnL = batchTrades.reduce((s, t) => s + (parseFloat(t.profit) || 0), 0);
    const pnlClass = totalPnL > 0 ? 'pos' : (totalPnL < 0 ? 'neg' : '');
    const pnlSign = totalPnL > 0 ? '+' : '';
    
    // Win Rate
    const wins = batchTrades.filter(t => (parseFloat(t.profit) || 0) > 0).length;
    const winRate = batchTrades.length > 0 ? (wins / batchTrades.length * 100).toFixed(1) : '0.0';

    // Avg target R:R
    let rrSum = 0;
    let rrCount = 0;
    for (let t of batchTrades) {
      const risk = Math.abs(t.open_price - t.sl);
      const reward = Math.abs(t.tp - t.open_price);
      if (risk > 0 && reward > 0 && t.sl > 0 && t.tp > 0) {
        rrSum += (reward / risk);
        rrCount++;
      }
    }
    const avgRR = rrCount > 0 ? '1:' + (rrSum / rrCount).toFixed(2) : '1:1.20';

    const batchId = `mt4Batch_${index}`;
    const headerId = `mt4BatchHeader_${index}`;

    const existingBatch = document.getElementById(batchId);
    const displayStyle = (existingBatch && existingBatch.style.display !== 'none') ? 'grid' : 'none';
    const arrowSymbol = (existingBatch && existingBatch.style.display !== 'none') ? '▼' : '▶';

    return `
      <div style="grid-column: 1 / -1; display: flex; flex-direction: column; gap: 8px;">
        <div id="${headerId}" onclick="toggleMt4Batch('${batchId}', '${headerId}')" style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: background 0.2s, border-color 0.2s; user-select: none;" onmouseover="this.style.background='rgba(0, 240, 255, 0.04)'; this.style.borderColor='rgba(0, 240, 255, 0.2)';" onmouseout="this.style.background='rgba(255, 255, 255, 0.02)'; this.style.borderColor='var(--border)';">
          <div style="display: flex; align-items: center; gap: 12px;">
            <span class="batch-arrow" style="font-size: 11px; color: var(--accent); font-family: var(--mono);">${arrowSymbol}</span>
            <strong style="font-size: 15px; color: var(--text);">Batch ${chunk.batchNum} <span style="font-size: 12px; font-weight: normal; color: var(--text3); font-family: var(--mono); margin-left: 6px;">(Trades ${chunk.startIdx} - ${chunk.endIdx} of ${sorted.length})</span></strong>
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
          ${batchTrades.map(renderTradeCard).join('')}
        </div>
      </div>
    `;
  }).join(''));
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
