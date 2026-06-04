// /api/events — upcoming earnings + macro event hard-flags
// GET /api/events?sym=NVDA&type=Stock
//   → { earnings: { type:"earnings", date:"2026-06-12", daysAway:8, label:"Earnings in 8 days" } | null,
//       macro: [ { type:"FOMC", date:"2026-06-17", daysAway:13 }, { type:"CPI", date:"2026-06-10", daysAway:6 } ] }
//
// Earnings come from Yahoo Finance quoteSummary (calendarEvents module) — same Yahoo
// pattern api/candles.js uses. FOMC / CPI dates are hard-coded from the Fed / BLS
// calendars (both publish their schedules well in advance).

export const config = { runtime: 'edge' };

// ── Hard-coded macro calendar ────────────────────────────────────────────────
// FOMC = the rate-decision day (2nd day of each 2-day meeting), per the Fed's
// published 2026 schedule. CPI = the BLS release day. Both are announced months
// ahead, so hard-coding the next ~6 months is safe and removes an API dependency.
const FOMC_DATES = ['2026-06-17', '2026-07-29', '2026-09-16', '2026-10-28', '2026-12-09', '2027-01-28'];
const CPI_DATES  = ['2026-06-10', '2026-07-14', '2026-08-12', '2026-09-11', '2026-10-13', '2026-11-13', '2026-12-10'];

function daysAway(dateStr) {
  const today = new Date(); today.setUTCHours(0, 0, 0, 0);
  const d = new Date(dateStr + 'T00:00:00Z');
  return Math.round((d.getTime() - today.getTime()) / 86400000);
}
function nextUpcoming(dates) {
  for (const d of dates) {
    const n = daysAway(d);
    if (n >= 0) return { date: d, daysAway: n };
  }
  return null;
}

const BROWSER_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Accept': 'application/json',
  'Accept-Language': 'en-US,en;q=0.9',
};

// Pull the next earnings date from Yahoo's quoteSummary calendarEvents module.
// Graceful: returns null on any failure (Yahoo sometimes gates this behind a crumb).
async function fetchEarnings(sym) {
  const ticker = sym.toUpperCase();
  const paths = [
    `https://query1.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}?modules=calendarEvents`,
    `https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}?modules=calendarEvents`,
  ];
  for (const url of paths) {
    try {
      const res = await fetch(url, { headers: BROWSER_HEADERS, signal: AbortSignal.timeout(9000) });
      if (!res.ok) continue;
      const json = await res.json();
      const earnings = json?.quoteSummary?.result?.[0]?.calendarEvents?.earnings;
      const arr = earnings?.earningsDate;
      if (!Array.isArray(arr) || !arr.length) continue;
      // Entries look like { raw: <epoch seconds>, fmt: "2026-06-12" }
      const first = arr[0];
      const raw = (first && typeof first === 'object') ? first.raw : first;
      if (!raw) continue;
      const dateStr = new Date(raw * 1000).toISOString().slice(0, 10);
      const n = daysAway(dateStr);
      if (n < 0) continue;   // already reported
      return { type: 'earnings', date: dateStr, daysAway: n, label: `Earnings in ${n} day${n === 1 ? '' : 's'}` };
    } catch { /* try next host */ }
  }
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
    'Cache-Control': 's-maxage=3600, stale-while-revalidate=7200',
  };

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (!sym) return new Response(JSON.stringify({ error: 'sym parameter required' }), { status: 400, headers: cors });

  // Nearest upcoming macro events (everyone gets these — they move the whole tape)
  const fomc = nextUpcoming(FOMC_DATES);
  const cpi  = nextUpcoming(CPI_DATES);
  const macro = [];
  if (fomc) macro.push({ type: 'FOMC', date: fomc.date, daysAway: fomc.daysAway });
  if (cpi)  macro.push({ type: 'CPI',  date: cpi.date,  daysAway: cpi.daysAway });

  // Earnings only apply to single stocks / ETFs
  let earnings = null;
  if (type === 'Stock' || type === 'ETF') {
    earnings = await fetchEarnings(sym).catch(() => null);
  }

  return new Response(JSON.stringify({ earnings, macro }), { headers: cors });
}
