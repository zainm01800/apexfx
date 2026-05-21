// /api/candles — Vercel serverless function
// Proxies Yahoo Finance chart API and returns clean OHLCV bars as JSON.
// Called by the frontend as: GET /api/candles?sym=AAPL&type=Stock&tf=1d&from=...&to=...

export const config = { runtime: 'edge' };

// Yahoo Finance hard limits per interval
const YF_LIMITS = {
  '1m':  { interval: '1m',  max_days: 7,    max_range_days: 1   },
  '5m':  { interval: '5m',  max_days: 60,   max_range_days: 7   },
  '15m': { interval: '15m', max_days: 60,   max_range_days: 14  },
  '30m': { interval: '30m', max_days: 60,   max_range_days: 20  },
  '1h':  { interval: '60m', max_days: 729,  max_range_days: 90  },
  '4h':  { interval: '60m', max_days: 729,  max_range_days: 90  },
  '1d':  { interval: '1d',  max_days: 3649, max_range_days: 3649},
  '1w':  { interval: '1wk', max_days: 3649, max_range_days: 3649},
  '1M':  { interval: '1mo', max_days: 3649, max_range_days: 3649},
};

function toYahooTicker(sym, type) {
  if (type === 'Forex') {
    const m = {
      'EUR/USD':'EURUSD=X','GBP/USD':'GBPUSD=X','USD/JPY':'JPY=X',
      'USD/CHF':'CHF=X','AUD/USD':'AUDUSD=X','NZD/USD':'NZDUSD=X',
      'USD/CAD':'CAD=X','GBP/JPY':'GBPJPY=X','EUR/GBP':'EURGBP=X',
    };
    return m[sym] || sym.replace('/', '') + '=X';
  }
  if (type === 'Crypto') return sym.replace('/', '-'); // BTC/USD → BTC-USD
  if (type === 'Futures') {
    const m = { 'ES1!':'ES=F','CL1!':'CL=F','GC1!':'GC=F','NQ1!':'NQ=F' };
    return m[sym] || sym;
  }
  return sym;
}

function isAllowedOrigin(origin, host) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.host === host) return true;
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return true;
  } catch {}
  return false;
}

function aggregateTo4h(bars) {
  const agg = [];
  for (let i = 0; i < bars.length; i += 4) {
    const c = bars.slice(i, i + 4);
    if (!c.length) continue;
    agg.push({
      time:   c[0].time,
      open:   c[0].open,
      high:   Math.max(...c.map(b => b.high)),
      low:    Math.min(...c.map(b => b.low)),
      close:  c[c.length - 1].close,
      volume: c.reduce((s, b) => s + b.volume, 0),
    });
  }
  return agg;
}

async function fetchYahooChunk(ticker, interval, from, to, dp) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}` +
    `?period1=${from}&period2=${to}&interval=${interval}&events=history&includePrePost=false`;

  const res = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'application/json',
      'Accept-Language': 'en-US,en;q=0.9',
    },
    signal: AbortSignal.timeout(12000),
  });

  if (!res.ok) return [];
  const json = await res.json();
  const result = json?.chart?.result?.[0];
  if (!result?.timestamp?.length) return [];

  const q = result.indicators.quote[0];
  return result.timestamp.map((t, i) => ({
    time:   t,
    open:   q.open[i]  != null ? +q.open[i].toFixed(dp)  : null,
    high:   q.high[i]  != null ? +q.high[i].toFixed(dp)  : null,
    low:    q.low[i]   != null ? +q.low[i].toFixed(dp)   : null,
    close:  q.close[i] != null ? +q.close[i].toFixed(dp) : null,
    volume: q.volume?.[i] || 0,
  })).filter(b => b.open && b.high && b.low && b.close);
}

export default async function handler(req) {
  const { searchParams } = new URL(req.url);
  const url = new URL(req.url);
  const origin = req.headers.get('origin');
  const sym  = searchParams.get('sym')  || 'AAPL';
  const type = searchParams.get('type') || 'Stock';
  const tf   = searchParams.get('tf')   || '1d';
  const allowedOrigin = isAllowedOrigin(origin, url.host) ? (origin || url.origin) : url.origin;

  const corsHeaders = {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Content-Type': 'application/json',
    // Short-TF data is fresher — cache aggressively for daily+, briefly for intraday
    'Cache-Control': tf === '1m' ? 's-maxage=30, stale-while-revalidate=60'
                   : tf === '5m' ? 's-maxage=60, stale-while-revalidate=120'
                   : ['15m','30m','1h'].includes(tf) ? 's-maxage=120, stale-while-revalidate=300'
                   : 's-maxage=300, stale-while-revalidate=600',
  };

  if (origin && !isAllowedOrigin(origin, url.host)) {
    return new Response(JSON.stringify({ error: 'Origin not allowed' }), { status: 403, headers: corsHeaders });
  }

  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const limits   = YF_LIMITS[tf] || YF_LIMITS['1d'];
  const interval = limits.interval;
  const dp       = type === 'Forex' ? 5 : 4;
  const ticker   = toYahooTicker(sym, type);

  const now      = Math.floor(Date.now() / 1000);
  const earliest = now - limits.max_days * 86400;

  let reqFrom = parseInt(searchParams.get('from') || '0') || earliest;
  let reqTo   = parseInt(searchParams.get('to')   || '0') || now;
  reqFrom = Math.max(reqFrom, earliest);
  reqTo   = Math.min(reqTo,   now);

  const maxChunk = limits.max_range_days * 86400;

  try {
    let allBars = [];

    if (reqTo - reqFrom <= maxChunk) {
      allBars = await fetchYahooChunk(ticker, interval, reqFrom, reqTo, dp);
    } else {
      // Break into chunks — no cap on count, fetch all available history
      const chunks = [];
      let chunkTo = reqTo;
      while (chunkTo > reqFrom) {
        const chunkFrom = Math.max(reqFrom, chunkTo - maxChunk);
        chunks.push({ from: chunkFrom, to: chunkTo });
        chunkTo = chunkFrom - 1;
      }
      // Fetch in parallel batches of 5 to avoid overwhelming Yahoo
      const results = [];
      for (let i = 0; i < chunks.length; i += 5) {
        const batch = chunks.slice(i, i + 5);
        const batchResults = await Promise.all(
          batch.map(ch => fetchYahooChunk(ticker, interval, ch.from, ch.to, dp).catch(() => []))
        );
        results.push(...batchResults);
      }
      allBars = results.flat();
    }

    if (!allBars.length) {
      return new Response(JSON.stringify([]), { status: 200, headers: corsHeaders });
    }

    // Deduplicate and sort
    const seen = new Map();
    allBars.forEach(b => seen.set(b.time, b));
    allBars = [...seen.values()].sort((a, b) => a.time - b.time);

    if (tf === '4h') allBars = aggregateTo4h(allBars);

    return new Response(JSON.stringify(allBars), { status: 200, headers: corsHeaders });

  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: corsHeaders });
  }
}
