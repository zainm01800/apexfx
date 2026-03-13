// /api/news — Vercel edge function
// Fetches news for Stocks, Forex, and Crypto via Finnhub.
// GET /api/news?sym=AAPL&type=Stock
// GET /api/news?sym=EUR/USD&type=Forex
// GET /api/news?sym=BTC/USD&type=Crypto

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY;
const FBASE = 'https://finnhub.io/api/v1';

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Content-Type': 'application/json',
  'Cache-Control': 's-maxage=300, stale-while-revalidate=600',
};

function today() {
  return new Date().toISOString().slice(0, 10);
}
function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function mapItem(i) {
  return {
    title:   i.headline,
    summary: i.summary || '',
    link:    i.url,
    date:    new Date(i.datetime * 1000).toISOString(),
    source:  i.source || 'Finnhub',
    image:   i.image || null,
  };
}

export default async function handler(req) {
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: cors });
  }

  if (!FINNHUB_KEY) {
    return new Response(JSON.stringify([]), { status: 200, headers: cors });
  }

  const { searchParams } = new URL(req.url);
  const sym  = (searchParams.get('sym')  || 'AAPL').trim();
  const type = (searchParams.get('type') || 'Stock').trim();

  try {
    let items = [];

    if (type === 'Stock' || type === 'ETF' || type === 'Futures' || type === 'Index') {
      // Company-specific news (last 14 days)
      const clean = sym.replace(/[^A-Z0-9.]/gi, '').toUpperCase();
      const url = `${FBASE}/company-news?symbol=${encodeURIComponent(clean)}&from=${daysAgo(14)}&to=${today()}&token=${FINNHUB_KEY}`;
      const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data) && data.length) {
          items = data.slice(0, 20).map(mapItem).filter(i => i.title);
        }
      }
      // Fallback to general market news
      if (!items.length) {
        const res2 = await fetch(`${FBASE}/news?category=general&token=${FINNHUB_KEY}`, { signal: AbortSignal.timeout(8000) });
        if (res2.ok) {
          const data2 = await res2.json();
          if (Array.isArray(data2)) items = data2.slice(0, 20).map(mapItem).filter(i => i.title);
        }
      }

    } else if (type === 'Forex') {
      const res = await fetch(`${FBASE}/news?category=forex&token=${FINNHUB_KEY}`, { signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          const [base, quote] = sym.split('/');
          const kws = [base, quote, sym, sym.replace('/', '')].map(k => k.toLowerCase());
          const scored = data.map(i => {
            const text = ((i.headline || '') + ' ' + (i.summary || '')).toLowerCase();
            return { ...i, _score: kws.filter(k => text.includes(k)).length };
          });
          scored.sort((a, b) => b._score - a._score || b.datetime - a.datetime);
          items = scored.slice(0, 20).map(mapItem).filter(i => i.title);
        }
      }

    } else if (type === 'Crypto') {
      const res = await fetch(`${FBASE}/news?category=crypto&token=${FINNHUB_KEY}`, { signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          const [base] = sym.split('/');
          const kws = [base, sym, sym.replace('/', '')].map(k => k.toLowerCase());
          const scored = data.map(i => {
            const text = ((i.headline || '') + ' ' + (i.summary || '')).toLowerCase();
            return { ...i, _score: kws.filter(k => text.includes(k)).length };
          });
          scored.sort((a, b) => b._score - a._score || b.datetime - a.datetime);
          items = scored.slice(0, 20).map(mapItem).filter(i => i.title);
        }
      }
    }

    return new Response(JSON.stringify(items), { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify([]), { status: 200, headers: cors });
  }
}
