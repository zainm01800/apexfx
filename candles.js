// /api/candles — Vercel serverless function
// Proxies Yahoo Finance chart API and returns clean OHLCV bars as JSON.
// Called by the frontend as: GET /api/candles?sym=AAPL&type=Stock&tf=1d&from=...&to=...

export const config = { runtime: 'edge' };

const STOOQ_DAILY = new Set(['1d', '1w', '1M']);

// Map app symbols → Yahoo Finance tickers
function toYahooTicker(sym, type) {
  if (type === 'Forex') {
    const m = {
      'EUR/USD': 'EURUSD=X', 'GBP/USD': 'GBPUSD=X', 'USD/JPY': 'JPY=X',
      'USD/CHF': 'CHF=X',    'AUD/USD': 'AUDUSD=X', 'NZD/USD': 'NZDUSD=X',
      'USD/CAD': 'CAD=X',    'GBP/JPY': 'GBPJPY=X', 'EUR/GBP': 'EURGBP=X',
    };
    return m[sym] || sym.replace('/', '') + '=X';
  }
  if (type === 'Futures') {
    const m = { 'ES1!': 'ES=F', 'CL1!': 'CL=F', 'GC1!': 'GC=F', 'NQ1!': 'NQ=F' };
    return m[sym] || sym;
  }
  return sym; // stocks/ETFs — pass through as-is
}

// Map app timeframes → Yahoo Finance intervals
function toYahooInterval(tf) {
  return { '1m':'1m','5m':'5m','15m':'15m','1h':'60m','4h':'60m','1d':'1d','1w':'1wk','1M':'1mo' }[tf] || '1d';
}

function aggregateTo4h(bars) {
  const agg = [];
  for (let i = 0; i < bars.length; i += 4) {
    const chunk = bars.slice(i, i + 4);
    if (!chunk.length) continue;
    agg.push({
      time:   chunk[0].time,
      open:   chunk[0].open,
      high:   Math.max(...chunk.map(b => b.high)),
      low:    Math.min(...chunk.map(b => b.low)),
      close:  chunk[chunk.length - 1].close,
      volume: chunk.reduce((s, b) => s + b.volume, 0),
    });
  }
  return agg;
}

export default async function handler(req) {
  const { searchParams } = new URL(req.url);
  const sym    = searchParams.get('sym')  || 'AAPL';
  const type   = searchParams.get('type') || 'Stock';
  const tf     = searchParams.get('tf')   || '1d';
  const period1 = searchParams.get('from') || String(Math.floor(Date.now()/1000) - 365*86400);
  const period2 = searchParams.get('to')   || String(Math.floor(Date.now()/1000));

  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=60, stale-while-revalidate=300',
  };

  const ticker   = toYahooTicker(sym, type);
  const interval = toYahooInterval(tf);
  const dp       = type === 'Forex' ? 5 : 4;

  // Yahoo Finance v8 chart endpoint — works server-side with no auth
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}` +
    `?period1=${period1}&period2=${period2}&interval=${interval}&events=history&includePrePost=false`;

  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; ApexFX/1.0)',
        'Accept': 'application/json',
      },
      signal: AbortSignal.timeout(12000),
    });

    if (!res.ok) {
      return new Response(JSON.stringify({ error: `Yahoo HTTP ${res.status}` }), { status: 502, headers: corsHeaders });
    }

    const json   = await res.json();
    const result = json?.chart?.result?.[0];
    const err    = json?.chart?.error;

    if (err || !result?.timestamp?.length) {
      return new Response(JSON.stringify([]), { status: 200, headers: corsHeaders });
    }

    const q = result.indicators.quote[0];
    let bars = result.timestamp.map((t, i) => ({
      time:   t,
      open:   q.open[i]   != null ? +q.open[i].toFixed(dp)   : null,
      high:   q.high[i]   != null ? +q.high[i].toFixed(dp)   : null,
      low:    q.low[i]    != null ? +q.low[i].toFixed(dp)    : null,
      close:  q.close[i]  != null ? +q.close[i].toFixed(dp)  : null,
      volume: q.volume?.[i] || 0,
    })).filter(b => b.open && b.high && b.low && b.close);

    // Aggregate 1h candles → 4h
    if (tf === '4h') bars = aggregateTo4h(bars);

    bars.sort((a, b) => a.time - b.time);

    return new Response(JSON.stringify(bars), { status: 200, headers: corsHeaders });

  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: corsHeaders });
  }
}
