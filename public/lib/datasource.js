// APEX backtest data source — candle adapter (MAIN THREAD; uses fetch).
// Today: Yahoo Finance via the existing /api/candles edge function. The provider
// shape (name + getCandles) is the swap point for a future Finnhub adapter — the
// worker/runner only depend on getMaxHistory()/getCandles(), not on Yahoo. → APEX.datasource
'use strict';

// Per-timeframe max history Yahoo will return (mirrors api/candles.js YF_LIMITS).
const MAX_DAYS_YAHOO = { '1m': 7, '5m': 60, '15m': 60, '30m': 60, '1h': 729, '4h': 729, '1d': 3649, '1w': 3649 };

// OANDA permits much deeper historical data for forex (up to 5,000 bars per call).
// We set 15m to 2 years (730 days) and 1h to 4 years (1460 days) to allow deep testing.
const MAX_DAYS_OANDA = { '1m': 30, '5m': 365, '15m': 730, '30m': 730, '1h': 1460, '4h': 1460, '1d': 3649, '1w': 3649 };

const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];

const yahooProvider = {
  name: 'yahoo',
  maxDays: (tf) => MAX_DAYS_YAHOO[tf] || 3649,
  // Fetch the maximum available history for sym/type/tf. Returns OHLCV bars
  // [{time,open,high,low,close,volume}] ascending, or throws.
  async getCandles(sym, type, tf, { signal } = {}) {
    const tfSec = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800 }[tf] || 86400;
    const to = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
    const from = to - (MAX_DAYS_YAHOO[tf] || 3649) * 86400;
    const url = `/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=${tf}&from=${from}&to=${to}`;
    const r = await fetch(url, { signal });
    if (!r.ok) throw new Error(`candles ${sym} ${tf}: HTTP ${r.status}`);
    const d = await r.json();
    if (d && d.error) throw new Error(`candles ${sym} ${tf}: ${d.error}`);
    return Array.isArray(d) ? d : [];
  },
};

const oandaProvider = {
  name: 'oanda',
  maxDays: (tf) => MAX_DAYS_OANDA[tf] || 3649,
  async getCandles(sym, type, tf, { signal } = {}) {
    const tfSec = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800 }[tf] || 86400;
    const to = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
    const from = to - (MAX_DAYS_OANDA[tf] || 3649) * 86400;
    const url = `/api/oanda-candles?sym=${encodeURIComponent(sym)}&tf=${tf}&from=${from}&to=${to}`;
    const r = await fetch(url, { signal });
    if (!r.ok) throw new Error(`OANDA candles ${sym} ${tf}: HTTP ${r.status}`);
    const d = await r.json();
    if (d && d.error) throw new Error(`OANDA candles ${sym} ${tf}: ${d.error}`);
    return Array.isArray(d) ? d : [];
  },
};

function getProvider(type) {
  return (type === 'Forex') ? oandaProvider : yahooProvider;
}

function getMaxHistory(tf, type) { 
  return getProvider(type).maxDays(tf); 
}

function getCandles(sym, type, tf, opts) { 
  return getProvider(type).getCandles(sym, type, tf, opts || {}); 
}

// Weekly context bars for the confluence strategy on 1d.
function getWeekly(sym, type, opts) { 
  return getProvider(type).getCandles(sym, type, '1w', opts || {}); 
}

const _datasource = { TIMEFRAMES, MAX_DAYS: MAX_DAYS_YAHOO, MAX_DAYS_OANDA, yahooProvider, oandaProvider, getProvider, getMaxHistory, getCandles, getWeekly };
(function (g) { g.APEX = g.APEX || {}; g.APEX.datasource = _datasource; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _datasource;
