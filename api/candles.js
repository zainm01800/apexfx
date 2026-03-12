// /api/candles — Vercel serverless function
// Proxies Yahoo Finance chart API and returns clean OHLCV bars as JSON.
// Called by the frontend as: GET /api/candles?sym=AAPL&type=Stock&tf=1d&from=...&to=...

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY;

// Yahoo Finance hard limits per interval
const YF_LIMITS = {
  '1m':  { interval: '1m',  max_days: 7,   max_range_days: 1   },
  '5m':  { interval: '5m',  max_days: 60,  max_range_days: 5   },
  '15m': { interval: '15m', max_days: 60,  max_range_days: 10  },
  '30m': { interval: '30m', max_days: 60,  max_range_days: 20  },
  '1h':  { interval: '60m', max_days: 730, max_range_days: 90  },
  '4h':  { interval: '60m', max_days: 730, max_range_days: 90  },
  '1d':  { interval: '1d',  max_days: 3650,max_range_days: 3650},
  '1w':  { interval: '1wk', max_days: 3650,max_range_days: 3650},
  '1M':  { interval: '1mo', max_days: 3650,max_range_days: 3650},
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
  if (type === 'Futures') {
    const m = { 'ES1!':'ES=F','CL1!':'CL=F','GC1!':'GC=F','NQ1!':'NQ=F' };
    return m[sym] || sym;
  }
  return sym;
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
    signal: AbortSignal.timeout(10000),
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
  const sym  = searchParams.get('sym')  || 'AAPL';
  const type = searchParams.get('type') || 'Stock';
  const tf   = searchParams.get('tf')   || '1d';

  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    'Cache-Control': tf === '1m' ? 's-maxage=30' : tf === '5m' ? 's-maxage=60' : 's-maxage=300, stale-while-revalidate=600',
  };

  const limits   = YF_LIMITS[tf] || YF_LIMITS['1d'];
  const interval = limits.interval;
  const dp       = type === 'Forex' ? 5 : 4;
  const ticker   = toYahooTicker(sym, type);

  const now      = Math.floor(Date.now() / 1000);
  const earliest = now - limits.max_days * 86400;

  // Clamp requested range to what Yahoo actually supports
  let reqFrom = parseInt(searchParams.get('from') || '0') || earliest;
  let reqTo   = parseInt(searchParams.get('to')   || '0') || now;
  reqFrom = Math.max(reqFrom, earliest);
  reqTo   = Math.min(reqTo, now);

  const maxChunk = limits.max_range_days * 86400;

  try {
    let allBars = [];

    if (reqTo - reqFrom <= maxChunk) {
      allBars = await fetchYahooChunk(ticker, interval, reqFrom, reqTo, dp);
    } else {
      // Break into chunks, fetch most recent first
      const chunks = [];
      let chunkTo = reqTo;
      while (chunkTo > reqFrom && chunks.length < 8) {
        const chunkFrom = Math.max(reqFrom, chunkTo - maxChunk);
        chunks.push({ from: chunkFrom, to: chunkTo });
        chunkTo = chunkFrom - 1;
      }
      const results = await Promise.all(
        chunks.map(c => fetchYahooChunk(ticker, interval, c.from, c.to, dp).catch(() => []))
      );
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
