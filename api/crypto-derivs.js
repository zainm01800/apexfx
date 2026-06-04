// /api/crypto-derivs — crypto-native positioning + market structure
// GET /api/crypto-derivs?sym=BTC/USD
//   → { funding, open_interest, long_short, dominance_pct, total_mcap_usd,
//       stablecoin_supply_usd, signal } | { derivs: null }
//
// The committee analyses crypto with stock/FX-style inputs only (price, RSI, MACD,
// news). This adds the data a real crypto desk lives on: perp FUNDING + OPEN
// INTEREST + LONG/SHORT positioning (squeeze/crowding read) and market STRUCTURE
// (BTC dominance, total mcap, stablecoin dry powder). All free, no API keys.
//
// Sources: Binance Futures public API (funding, OI, OI trend, global long/short
// account ratio) with OKX as a funding fallback; CoinGecko for dominance/mcap/
// stablecoin supply. Every piece degrades gracefully to null on failure.

export const config = { runtime: 'edge' };

const UA = { 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json' };
const jget = async (url, ms = 9000) => {
  try {
    const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(ms) });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
};

// Crypto symbol → base coin (BTC/USD → BTC, ETH-USDT → ETH).
function baseCoin(sym) {
  return sym.toUpperCase().trim().replace(/[/\-](USDT?|USD)$/, '').replace(/USDT?$/, '').replace(/[/\-]/g, '');
}

const COINGECKO_ID = {
  BTC: 'bitcoin', ETH: 'ethereum', SOL: 'solana', BNB: 'binancecoin', XRP: 'ripple',
  ADA: 'cardano', AVAX: 'avalanche-2', DOGE: 'dogecoin', MATIC: 'matic-network',
  LINK: 'chainlink', ARB: 'arbitrum', SUI: 'sui', LTC: 'litecoin', DOT: 'polkadot',
};

// ── Perp positioning (Binance primary, OKX funding fallback) ──────────────────
async function fetchPerp(base) {
  const perp = base + 'USDT';
  const [premium, oiNow, oiHist, lsr] = await Promise.all([
    jget(`https://fapi.binance.com/fapi/v1/premiumIndex?symbol=${perp}`),
    jget(`https://fapi.binance.com/fapi/v1/openInterest?symbol=${perp}`),
    jget(`https://fapi.binance.com/futures/data/openInterestHist?symbol=${perp}&period=1d&limit=8`),
    jget(`https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=${perp}&period=1d&limit=1`),
  ]);

  // Funding (Binance per-8h rate; fall back to OKX if Binance is unavailable/geo-blocked)
  let rate8h = (premium && premium.lastFundingRate != null) ? parseFloat(premium.lastFundingRate) : null;
  let fundingSource = rate8h != null ? 'Binance' : null;
  if (rate8h == null) {
    const okx = await jget(`https://www.okx.com/api/v5/public/funding-rate?instId=${base}-USDT-SWAP`);
    const fr = okx?.data?.[0]?.fundingRate;
    if (fr != null) { rate8h = parseFloat(fr); fundingSource = 'OKX'; }
  }
  let funding = null;
  if (rate8h != null && !isNaN(rate8h)) {
    const pct8h = +(rate8h * 100).toFixed(4);
    const annualized = +(rate8h * 3 * 365 * 100).toFixed(1);
    const label = pct8h > 0.03 ? 'elevated positive — longs paying (crowded long, squeeze-DOWN risk)'
      : pct8h < -0.01 ? 'negative — shorts paying (crowded short, squeeze-UP risk)'
      : 'neutral';
    funding = { rate_8h_pct: pct8h, annualized_pct: annualized, label, source: fundingSource };
  }

  // Open interest + 7-day trend
  let open_interest = null;
  const oiBtc = oiNow && oiNow.openInterest != null ? parseFloat(oiNow.openInterest) : null;
  if (Array.isArray(oiHist) && oiHist.length >= 2) {
    const first = parseFloat(oiHist[0].sumOpenInterest);
    const last = parseFloat(oiHist[oiHist.length - 1].sumOpenInterest);
    const usd = parseFloat(oiHist[oiHist.length - 1].sumOpenInterestValue);
    const chg = first > 0 ? +(((last - first) / first) * 100).toFixed(1) : null;
    open_interest = {
      btc: oiBtc != null ? +oiBtc.toFixed(0) : +last.toFixed(0),
      usd: usd ? Math.round(usd) : null,
      change_7d_pct: chg,
      label: chg == null ? null : chg > 5 ? 'rising (fresh leverage building)' : chg < -5 ? 'falling (deleveraging / positions closing)' : 'flat',
    };
  } else if (oiBtc != null) {
    open_interest = { btc: +oiBtc.toFixed(0), usd: null, change_7d_pct: null, label: null };
  }

  // Global long/short ACCOUNT ratio (retail crowding — contrarian at extremes)
  let long_short = null;
  const lr = Array.isArray(lsr) && lsr[0] ? parseFloat(lsr[0].longShortRatio) : null;
  if (lr != null && !isNaN(lr)) {
    const pctLong = Math.round((lr / (1 + lr)) * 100);
    long_short = {
      account_ratio: +lr.toFixed(2),
      pct_long: pctLong,
      label: pctLong >= 65 ? 'crowd heavily LONG (contrarian caution)' : pctLong <= 35 ? 'crowd heavily SHORT (contrarian caution)' : 'balanced',
    };
  }

  return { perp, funding, open_interest, long_short };
}

// ── Market structure (CoinGecko: dominance, total mcap, stablecoin supply) ────
async function fetchStructure() {
  const [global, stables] = await Promise.all([
    jget('https://api.coingecko.com/api/v3/global'),
    jget('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=tether,usd-coin,dai'),
  ]);
  const g = global?.data;
  const dominance_pct = g?.market_cap_percentage?.btc != null ? +g.market_cap_percentage.btc.toFixed(1) : null;
  const total_mcap_usd = g?.total_market_cap?.usd != null ? Math.round(g.total_market_cap.usd) : null;
  let stablecoin_supply_usd = null;
  if (Array.isArray(stables)) {
    const sum = stables.reduce((s, c) => s + (c.market_cap || 0), 0);
    if (sum > 0) stablecoin_supply_usd = Math.round(sum);
  }
  return { dominance_pct, total_mcap_usd, stablecoin_supply_usd };
}

function fmtUsdShort(n) {
  if (n == null) return 'n/a';
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9)  return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6)  return '$' + (n / 1e6).toFixed(0) + 'M';
  return '$' + n.toLocaleString();
}

export default async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin');
  const sym    = (url.searchParams.get('sym') || '').trim();

  const cors = {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=300, stale-while-revalidate=600',
  };

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (!sym) return new Response(JSON.stringify({ error: 'sym parameter required' }), { status: 400, headers: cors });

  const base = baseCoin(sym);
  try {
    const [perp, structure] = await Promise.all([fetchPerp(base), fetchStructure()]);

    // Nothing usable at all → null so the frontend simply skips the block.
    if (!perp.funding && !perp.open_interest && !perp.long_short && structure.dominance_pct == null) {
      return new Response(JSON.stringify({ derivs: null }), { headers: cors });
    }

    // Human-readable one-liner for the committee prompt.
    const parts = [];
    if (perp.funding) parts.push(`Perp funding ${perp.funding.rate_8h_pct > 0 ? '+' : ''}${perp.funding.rate_8h_pct}%/8h (~${perp.funding.annualized_pct > 0 ? '+' : ''}${perp.funding.annualized_pct}%/yr) — ${perp.funding.label}.`);
    if (perp.open_interest) parts.push(`Open interest ${perp.open_interest.btc.toLocaleString()} ${base}${perp.open_interest.usd ? ` (${fmtUsdShort(perp.open_interest.usd)})` : ''}${perp.open_interest.change_7d_pct != null ? `, ${perp.open_interest.change_7d_pct > 0 ? '+' : ''}${perp.open_interest.change_7d_pct}% over 7d — ${perp.open_interest.label}` : ''}.`);
    if (perp.long_short) parts.push(`Retail accounts ${perp.long_short.pct_long}% long (L/S ratio ${perp.long_short.account_ratio}) — ${perp.long_short.label}.`);
    if (structure.dominance_pct != null) parts.push(`BTC dominance ${structure.dominance_pct}%.`);
    if (structure.stablecoin_supply_usd != null) parts.push(`Stablecoin dry powder ${fmtUsdShort(structure.stablecoin_supply_usd)}.`);

    return new Response(JSON.stringify({
      source: 'Binance Futures + CoinGecko',
      base,
      perp: perp.perp,
      funding: perp.funding,
      open_interest: perp.open_interest,
      long_short: perp.long_short,
      dominance_pct: structure.dominance_pct,
      total_mcap_usd: structure.total_mcap_usd,
      stablecoin_supply_usd: structure.stablecoin_supply_usd,
      signal: parts.join(' '),
    }), { headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ derivs: null, error: e.message }), { headers: cors });
  }
}
