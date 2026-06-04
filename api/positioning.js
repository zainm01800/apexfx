// /api/positioning — Commitment of Traders (COT) speculative positioning
// GET /api/positioning?sym=EUR/USD&type=Forex
//   → { source, as_of, asset, noncomm_long, noncomm_short, net, pct_long,
//       net_change_wk, signal } | { positioning: null }
//
// Source: CFTC public reporting (Socrata) "Legacy Futures-Only" COT report,
// dataset 6dca-aqww. Free, no key. Covers FX majors, gold/silver/crude, the
// S&P/Nasdaq e-minis and CME Bitcoin. For single stocks there is no COT — the
// existing analyst-rating + insider-sentiment data in /api/quote already serves
// as positioning, so this returns null for them.

export const config = { runtime: 'edge' };

const CFTC_URL = 'https://publicreporting.cftc.gov/resource/6dca-aqww.json';

// symbol → CFTC contract_market_name (exact) + a human label.
// `inverse: true` means the COT asset is the QUOTE currency of a USD-base pair,
// so net-long that asset implies DOWNWARD pressure on the quoted pair.
const COT_MAP = {
  'EUR/USD': { name: 'EURO FX',             asset: 'Euro' },
  'GBP/USD': { name: 'BRITISH POUND',       asset: 'British Pound' },
  'AUD/USD': { name: 'AUSTRALIAN DOLLAR',   asset: 'Australian Dollar' },
  'NZD/USD': { name: 'NEW ZEALAND DOLLAR',  asset: 'New Zealand Dollar' },
  'USD/JPY': { name: 'JAPANESE YEN',        asset: 'Japanese Yen',      inverse: true },
  'USD/CHF': { name: 'SWISS FRANC',         asset: 'Swiss Franc',       inverse: true },
  'USD/CAD': { name: 'CANADIAN DOLLAR',     asset: 'Canadian Dollar',   inverse: true },
  'BTC/USD': { name: 'BITCOIN',             asset: 'Bitcoin' },
  'GLD':  { name: 'GOLD',                    asset: 'Gold' },
  'GC1!': { name: 'GOLD',                    asset: 'Gold' },
  'SLV':  { name: 'SILVER',                  asset: 'Silver' },
  'SI1!': { name: 'SILVER',                  asset: 'Silver' },
  'USO':  { name: 'CRUDE OIL, LIGHT SWEET-WTI', asset: 'WTI Crude Oil' },
  'CL1!': { name: 'CRUDE OIL, LIGHT SWEET-WTI', asset: 'WTI Crude Oil' },
  'SPY':  { name: 'E-MINI S&P 500',         asset: 'S&P 500' },
  'ES1!': { name: 'E-MINI S&P 500',         asset: 'S&P 500' },
  'QQQ':  { name: 'NASDAQ MINI',            asset: 'Nasdaq 100' },
  'NQ1!': { name: 'NASDAQ MINI',            asset: 'Nasdaq 100' },
};

function resolveContract(sym, type) {
  const s = sym.toUpperCase().trim();
  if (COT_MAP[s]) return COT_MAP[s];
  // Crypto written as BTC or BTC-USD
  if (type === 'Crypto' && /^BTC([/-]USD)?$/.test(s)) return COT_MAP['BTC/USD'];
  return null;
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
    'Cache-Control': 's-maxage=21600, stale-while-revalidate=43200',   // COT is weekly
  };

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (!sym) return new Response(JSON.stringify({ error: 'sym parameter required' }), { status: 400, headers: cors });

  const contract = resolveContract(sym, type);
  if (!contract) return new Response(JSON.stringify({ positioning: null }), { headers: cors });

  try {
    // Two most recent weekly reports → latest positioning + week-over-week change
    const q = `${CFTC_URL}?contract_market_name=${encodeURIComponent(contract.name)}` +
      `&%24order=report_date_as_yyyy_mm_dd%20DESC&%24limit=2`;
    const res = await fetch(q, { headers: { 'Accept': 'application/json' }, signal: AbortSignal.timeout(10000) });
    if (!res.ok) return new Response(JSON.stringify({ positioning: null }), { headers: cors });
    const rows = await res.json();
    if (!Array.isArray(rows) || !rows.length) return new Response(JSON.stringify({ positioning: null }), { headers: cors });

    const r = rows[0];
    const long  = parseInt(r.noncomm_positions_long_all, 10);
    const short = parseInt(r.noncomm_positions_short_all, 10);
    if (isNaN(long) || isNaN(short) || (long + short) === 0) {
      return new Response(JSON.stringify({ positioning: null }), { headers: cors });
    }
    const net    = long - short;
    const pctLong = Math.round(long / (long + short) * 100);
    const asOf   = (r.report_date_as_yyyy_mm_dd || '').slice(0, 10);

    let netChangeWk = null;
    if (rows[1]) {
      const pl = parseInt(rows[1].noncomm_positions_long_all, 10);
      const ps = parseInt(rows[1].noncomm_positions_short_all, 10);
      if (!isNaN(pl) && !isNaN(ps)) netChangeWk = net - (pl - ps);
    }

    const stance = net > 0 ? 'NET LONG' : net < 0 ? 'NET SHORT' : 'NEUTRAL';
    const extreme = pctLong >= 75 ? ' — crowded long (contrarian caution)'
                  : pctLong <= 25 ? ' — crowded short (contrarian caution)' : '';
    const wkStr = netChangeWk != null
      ? `, ${netChangeWk >= 0 ? '+' : ''}${netChangeWk.toLocaleString()} contracts WoW`
      : '';
    const inverseNote = contract.inverse
      ? ` (this is positioning on ${contract.asset}; net-long ${contract.asset} implies downside pressure on ${sym.toUpperCase()})`
      : '';

    const signal = `Large speculators are ${stance} ${contract.asset} futures: ${pctLong}% long ` +
      `(net ${net >= 0 ? '+' : ''}${net.toLocaleString()} contracts${wkStr})${extreme}${inverseNote}.`;

    return new Response(JSON.stringify({
      source: 'CFTC COT (weekly, large speculators)',
      as_of: asOf,
      asset: contract.asset,
      inverse: !!contract.inverse,
      noncomm_long: long,
      noncomm_short: short,
      net,
      pct_long: pctLong,
      net_change_wk: netChangeWk,
      signal,
    }), { headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ positioning: null, error: e.message }), { headers: cors });
  }
}
