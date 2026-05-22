// /api/search — Symbol search proxy
// GET /api/search?q=apple  →  [{symbol, name, type, exchange}]
// Combines Finnhub symbol search (stocks/ETFs) with a curated crypto+forex list.

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY;

// ── Curated crypto & forex for instant matching ───────────────────────────────
const CRYPTO = [
  { s: 'BTC/USD',  n: 'Bitcoin',        t: 'Crypto' },
  { s: 'ETH/USD',  n: 'Ethereum',       t: 'Crypto' },
  { s: 'SOL/USD',  n: 'Solana',         t: 'Crypto' },
  { s: 'BNB/USD',  n: 'BNB',            t: 'Crypto' },
  { s: 'XRP/USD',  n: 'Ripple',         t: 'Crypto' },
  { s: 'ADA/USD',  n: 'Cardano',        t: 'Crypto' },
  { s: 'DOGE/USD', n: 'Dogecoin',       t: 'Crypto' },
  { s: 'AVAX/USD', n: 'Avalanche',      t: 'Crypto' },
  { s: 'MATIC/USD',n: 'Polygon',        t: 'Crypto' },
  { s: 'DOT/USD',  n: 'Polkadot',       t: 'Crypto' },
  { s: 'LINK/USD', n: 'Chainlink',      t: 'Crypto' },
  { s: 'LTC/USD',  n: 'Litecoin',       t: 'Crypto' },
  { s: 'UNI/USD',  n: 'Uniswap',        t: 'Crypto' },
  { s: 'ATOM/USD', n: 'Cosmos',         t: 'Crypto' },
  { s: 'ARB/USD',  n: 'Arbitrum',       t: 'Crypto' },
  { s: 'SUI/USD',  n: 'Sui',            t: 'Crypto' },
  { s: 'APT/USD',  n: 'Aptos',          t: 'Crypto' },
  { s: 'INJ/USD',  n: 'Injective',      t: 'Crypto' },
  { s: 'OP/USD',   n: 'Optimism',       t: 'Crypto' },
  { s: 'SHIB/USD', n: 'Shiba Inu',      t: 'Crypto' },
];

const FOREX = [
  { s: 'EUR/USD', n: 'Euro / US Dollar',           t: 'Forex' },
  { s: 'GBP/USD', n: 'British Pound / US Dollar',  t: 'Forex' },
  { s: 'USD/JPY', n: 'US Dollar / Japanese Yen',   t: 'Forex' },
  { s: 'USD/CHF', n: 'US Dollar / Swiss Franc',    t: 'Forex' },
  { s: 'AUD/USD', n: 'Australian Dollar / USD',    t: 'Forex' },
  { s: 'USD/CAD', n: 'US Dollar / Canadian Dollar',t: 'Forex' },
  { s: 'NZD/USD', n: 'New Zealand Dollar / USD',   t: 'Forex' },
  { s: 'GBP/JPY', n: 'British Pound / Japanese Yen',t: 'Forex'},
  { s: 'EUR/GBP', n: 'Euro / British Pound',       t: 'Forex' },
  { s: 'EUR/JPY', n: 'Euro / Japanese Yen',        t: 'Forex' },
  { s: 'USD/MXN', n: 'US Dollar / Mexican Peso',   t: 'Forex' },
  { s: 'USD/ZAR', n: 'US Dollar / South African Rand', t: 'Forex' },
];

const CURATED = [...CRYPTO, ...FOREX];

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };
}

// Type label normaliser for Finnhub results
function normaliseType(t) {
  if (!t) return 'Stock';
  const u = t.toUpperCase();
  if (u === 'ETP' || u === 'ETF') return 'ETF';
  if (u === 'COMMON STOCK' || u === 'ORDINARY SHARES') return 'Stock';
  if (u === 'REIT') return 'REIT';
  if (u === 'DR') return 'ADR';
  return 'Stock';
}

// Only keep symbols that look like clean US tickers (no dots, no foreign exchange suffix)
function isCleanTicker(sym) {
  return /^[A-Z]{1,6}$/.test(sym);
}

export default async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin') || '';
  const headers = corsHeaders(origin);

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers });
  if (req.method !== 'GET')    return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers });

  const q = (url.searchParams.get('q') || '').trim().toUpperCase();
  if (!q || q.length < 1) return new Response(JSON.stringify([]), { status: 200, headers });

  // 1. Match against curated crypto + forex list first
  const curatedMatches = CURATED.filter(
    c => c.s.includes(q) || c.n.toUpperCase().includes(q)
  ).map(c => ({ symbol: c.s, name: c.n, type: c.t, exchange: '' }));

  // 2. Query Finnhub for stocks/ETFs
  let stockMatches = [];
  if (FINNHUB_KEY) {
    try {
      const res = await fetch(
        `https://finnhub.io/api/v1/search?q=${encodeURIComponent(q)}&token=${FINNHUB_KEY}`,
        { signal: AbortSignal.timeout(4000) }
      );
      if (res.ok) {
        const data = await res.json();
        stockMatches = (data.result || [])
          .filter(r => isCleanTicker(r.symbol))          // US clean tickers only
          .filter(r => ['Common Stock','ETP','ETF','REIT','DR'].includes(r.type))
          .slice(0, 12)
          .map(r => ({
            symbol:   r.symbol,
            name:     r.description || r.symbol,
            type:     normaliseType(r.type),
            exchange: r.primaryExchange || '',
          }));
      }
    } catch (_) {
      // Finnhub timeout — still return curated matches
    }
  }

  // 3. Merge: curated first, then stocks, deduplicate
  const seen = new Set();
  const results = [...curatedMatches, ...stockMatches].filter(r => {
    if (seen.has(r.symbol)) return false;
    seen.add(r.symbol);
    return true;
  }).slice(0, 10);

  return new Response(JSON.stringify(results), { status: 200, headers });
}
