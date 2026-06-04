// /api/sector — sector relative strength
// GET /api/sector?sym=NVDA&type=Stock
//   → { stock_return_30d: 14.2, sector_return_30d: 8.1, outperforming: true,
//       vs_sector: "+6.1%", sector: "Semiconductors (SMH)" }
//
// Maps a stock to its sector ETF and compares 30-day returns (both fetched from the
// Yahoo Finance chart API — the same pattern api/candles.js uses). For forex it
// benchmarks against DXY (the dollar index); for crypto against BTC.

export const config = { runtime: 'edge' };

// ── Top-50-ish stock → sector ETF map ────────────────────────────────────────
const STOCK_SECTOR = {
  // Semiconductors (SMH)
  NVDA: 'SMH', AMD: 'SMH', TSM: 'SMH', AVGO: 'SMH', QCOM: 'SMH', MU: 'SMH',
  INTC: 'SMH', ASML: 'SMH', AMAT: 'SMH', LRCX: 'SMH', TXN: 'SMH', ARM: 'SMH', SMCI: 'SMH',
  // Technology (XLK)
  AAPL: 'XLK', MSFT: 'XLK', ADBE: 'XLK', CRM: 'XLK', ORCL: 'XLK', CSCO: 'XLK',
  IBM: 'XLK', NOW: 'XLK', INTU: 'XLK', ACN: 'XLK', PLTR: 'XLK',
  // Communication services (XLC)
  GOOGL: 'XLC', GOOG: 'XLC', META: 'XLC', NFLX: 'XLC', DIS: 'XLC', CMCSA: 'XLC', T: 'XLC', VZ: 'XLC',
  // Consumer discretionary (XLY)
  AMZN: 'XLY', TSLA: 'XLY', HD: 'XLY', NKE: 'XLY', MCD: 'XLY', SBUX: 'XLY', LOW: 'XLY', BKNG: 'XLY',
  // Financials (XLF)
  JPM: 'XLF', BAC: 'XLF', WFC: 'XLF', GS: 'XLF', MS: 'XLF', C: 'XLF', V: 'XLF', MA: 'XLF', AXP: 'XLF',
  // Health care (XLV)
  UNH: 'XLV', JNJ: 'XLV', LLY: 'XLV', PFE: 'XLV', MRK: 'XLV', ABBV: 'XLV', TMO: 'XLV', ABT: 'XLV',
  // Energy (XLE)
  XOM: 'XLE', CVX: 'XLE', COP: 'XLE', SLB: 'XLE',
  // Industrials (XLI)
  BA: 'XLI', CAT: 'XLI', GE: 'XLI', HON: 'XLI', UPS: 'XLI', MMM: 'XLI',
  // Consumer staples (XLP)
  PG: 'XLP', KO: 'XLP', PEP: 'XLP', WMT: 'XLP', COST: 'XLP',
};

const ETF_NAME = {
  SMH: 'Semiconductors (SMH)', XLK: 'Technology (XLK)', XLC: 'Communications (XLC)',
  XLY: 'Consumer Discretionary (XLY)', XLF: 'Financials (XLF)', XLV: 'Health Care (XLV)',
  XLE: 'Energy (XLE)', XLI: 'Industrials (XLI)', XLP: 'Consumer Staples (XLP)',
};

const BROWSER_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Accept': 'application/json',
  'Accept-Language': 'en-US,en;q=0.9',
};

// Map an instrument to its Yahoo ticker (mirrors api/candles.js).
function toYahooTicker(sym, type) {
  const s = sym.toUpperCase();
  if (type === 'Forex') {
    const m = {
      'EUR/USD': 'EURUSD=X', 'GBP/USD': 'GBPUSD=X', 'USD/JPY': 'JPY=X',
      'USD/CHF': 'CHF=X', 'AUD/USD': 'AUDUSD=X', 'NZD/USD': 'NZDUSD=X',
      'USD/CAD': 'CAD=X', 'GBP/JPY': 'GBPJPY=X', 'EUR/GBP': 'EURGBP=X',
    };
    return m[s] || s.replace('/', '') + '=X';
  }
  if (type === 'Crypto') return s.replace('/', '-');   // BTC/USD → BTC-USD
  return s;
}

// 30-day (calendar) return from Yahoo daily candles. ~21 trading bars ≈ 30 days.
async function fetch30dReturn(ticker) {
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 45 * 86400;
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}` +
      `?period1=${from}&period2=${to}&interval=1d&events=history`;
    const res = await fetch(url, { headers: BROWSER_HEADERS, signal: AbortSignal.timeout(10000) });
    if (!res.ok) return null;
    const json = await res.json();
    const result = json?.chart?.result?.[0];
    const closes = (result?.indicators?.quote?.[0]?.close || []).filter(x => x != null);
    if (closes.length < 5) return null;
    const last = closes[closes.length - 1];
    const idx  = Math.max(0, closes.length - 22);   // ~30 calendar days back
    const past = closes[idx];
    if (!past) return null;
    return +(((last - past) / past) * 100).toFixed(2);
  } catch { return null; }
}

function fmtVs(diff) {
  return `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}%`;
}

export default async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin');
  const sym    = (url.searchParams.get('sym') || '').trim();
  const type   = url.searchParams.get('type') || 'Stock';

  const cors = {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=900, stale-while-revalidate=1800',
  };

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (!sym) return new Response(JSON.stringify({ error: 'sym parameter required' }), { status: 400, headers: cors });

  const s = sym.toUpperCase();

  // ── Pick the benchmark + label for this instrument ──
  let benchTicker, sectorLabel;
  if (type === 'Forex') {
    benchTicker = 'DX-Y.NYB'; sectorLabel = 'US Dollar Index (DXY)';
  } else if (type === 'Crypto') {
    const base = s.replace(/[/\-](USDT?|USD)$/, '').replace(/USDT?$/, '');
    if (base === 'BTC') {
      // BTC vs itself is meaningless — nothing to compare against.
      return new Response(JSON.stringify({ sector: null, reason: 'no benchmark for BTC' }), { headers: cors });
    }
    benchTicker = 'BTC-USD'; sectorLabel = 'Bitcoin (BTC)';
  } else {
    const etf = STOCK_SECTOR[s];
    if (!etf) {
      // Unknown sector mapping — benchmark against the broad market instead.
      benchTicker = 'SPY'; sectorLabel = 'S&P 500 (SPY)';
    } else {
      benchTicker = etf; sectorLabel = ETF_NAME[etf] || etf;
    }
  }

  try {
    const [stockRet, benchRet] = await Promise.all([
      fetch30dReturn(toYahooTicker(s, type)),
      fetch30dReturn(benchTicker),
    ]);

    if (stockRet == null || benchRet == null) {
      return new Response(JSON.stringify({ sector: sectorLabel, stock_return_30d: stockRet, sector_return_30d: benchRet, error: 'incomplete data' }), { headers: cors });
    }

    const diff = +(stockRet - benchRet).toFixed(2);
    return new Response(JSON.stringify({
      stock_return_30d: stockRet,
      sector_return_30d: benchRet,
      outperforming: diff >= 0,
      vs_sector: fmtVs(diff),
      sector: sectorLabel,
    }), { headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
  }
}
