// /api/quality-scores
// GET /api/quality-scores?sym=AAPL
// Computes institutional-grade accounting quality scores from Financial Modeling Prep (FMP):
//   • Piotroski F-Score (0–9): financial health & quality
//   • Beneish M-Score: earnings manipulation detection (>-1.78 = risk flag)
//   • Accrual Ratio: cash vs. GAAP earnings quality
//   • Altman Z-Score: bankruptcy risk proxy
//
// Requires FMP_API_KEY env var (free tier: 250 calls/day at financialmodelingprep.com)
// Results are cached 24hrs — data only changes quarterly with new filings.

export const config = { runtime: 'edge' };

const FMP_KEY  = process.env.FMP_API_KEY;
const FMP_BASE = 'https://financialmodelingprep.com/api/v3';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Content-Type': 'application/json',
  'Cache-Control': 'public, s-maxage=86400, stale-while-revalidate=172800',
};

async function fmpFetch(endpoint) {
  const sep = endpoint.includes('?') ? '&' : '?';
  const res = await fetch(`${FMP_BASE}${endpoint}${sep}apikey=${FMP_KEY}`, {
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return Array.isArray(data) && data.length ? data : null;
}

// ── Piotroski F-Score (0–9) ───────────────────────────────────────────────────
// Ref: Piotroski (2000) "Value Investing: The Use of Historical Financial Statement Information"
function calcPiotroski(is, bs, cf) {
  if (!is || is.length < 2 || !bs || bs.length < 2 || !cf || cf.length < 2) return null;
  const [i1, i0] = is;   // i1 = most recent year
  const [b1, b0] = bs;
  const [c1, c0] = cf;

  const ta1 = b1.totalAssets || 1;
  const ta0 = b0.totalAssets || 1;

  // Operating cash flow — FMP field names vary slightly
  const ocf1 = c1.operatingCashFlow ?? c1.netCashProvidedByOperatingActivities ?? 0;
  const ocf0 = c0.operatingCashFlow ?? c0.netCashProvidedByOperatingActivities ?? 0;

  const roa1 = (i1.netIncome || 0) / ta1;
  const roa0 = (i0.netIncome || 0) / ta0;

  const ltDebt1 = (b1.longTermDebt || 0) + (b1.longTermDebtNoncurrent || 0);
  const ltDebt0 = (b0.longTermDebt || 0) + (b0.longTermDebtNoncurrent || 0);
  const ltLev1  = ltDebt1 / ta1;
  const ltLev0  = ltDebt0 / ta0;

  const cr1 = (b1.totalCurrentAssets || 0) / Math.max(b1.totalCurrentLiabilities || 1, 1);
  const cr0 = (b0.totalCurrentAssets || 0) / Math.max(b0.totalCurrentLiabilities || 1, 1);

  const gm1 = (i1.grossProfit || 0) / Math.max(i1.revenue || 1, 1);
  const gm0 = (i0.grossProfit || 0) / Math.max(i0.revenue || 1, 1);

  const at1 = (i1.revenue || 0) / ta1;
  const at0 = (i0.revenue || 0) / ta0;

  // Shares: detect dilution (>2% increase in weighted avg shares)
  const sh1 = i1.weightedAverageShsOut || i1.weightedAverageShsOutDil || b1.commonStock || 0;
  const sh0 = i0.weightedAverageShsOut || i0.weightedAverageShsOutDil || b0.commonStock || 0;

  const signals = {
    f1_roa_positive:          roa1 > 0 ? 1 : 0,
    f2_ocf_positive:          ocf1 > 0 ? 1 : 0,
    f3_roa_improving:         roa1 > roa0 ? 1 : 0,
    f4_accrual_quality:       (ocf1 / ta1) > roa1 ? 1 : 0,    // cash earnings > accrual
    f5_leverage_decreasing:   ltLev1 <= ltLev0 ? 1 : 0,
    f6_liquidity_improving:   cr1 >= cr0 ? 1 : 0,
    f7_no_dilution:           sh0 === 0 || sh1 <= sh0 * 1.02 ? 1 : 0,
    f8_gross_margin_improving: gm1 >= gm0 ? 1 : 0,
    f9_asset_turnover_improving: at1 >= at0 ? 1 : 0,
  };

  const score = Object.values(signals).reduce((s, v) => s + v, 0);
  const quality =
    score >= 8 ? 'high-quality' :
    score >= 6 ? 'above-average' :
    score >= 4 ? 'average' :
    score >= 2 ? 'weak' : 'distressed';

  const interpretation =
    score >= 8 ? `F-Score ${score}/9: strong fundamental quality — statistically associated with outperformance` :
    score >= 6 ? `F-Score ${score}/9: above-average quality — solid but not exceptional` :
    score >= 4 ? `F-Score ${score}/9: average quality — mixed signals, no clear edge` :
    score >= 2 ? `F-Score ${score}/9: weak fundamentals — avoid or reduce exposure` :
                 `F-Score ${score}/9: distressed — multiple red flags, elevated risk`;

  return { score, max: 9, quality, interpretation, signals };
}

// ── Beneish M-Score ───────────────────────────────────────────────────────────
// Ref: Beneish (1999) "The Detection of Earnings Manipulation"
// Threshold: > -1.78 = potential manipulation. 76% accuracy, 17.5% false positive rate.
function calcBeneish(is, bs, cf) {
  if (!is || is.length < 2 || !bs || bs.length < 2 || !cf || cf.length < 2) return null;
  const [i1, i0] = is;
  const [b1, b0] = bs;
  const [c1]     = cf;

  const safe = (n, d) => (d && isFinite(d) && d !== 0) ? n / d : null;

  const r0   = i0.revenue || 0, r1   = i1.revenue || 0;
  const rec0 = b0.netReceivables || 0, rec1 = b1.netReceivables || 0;
  const gp0  = i0.grossProfit   || 0, gp1  = i1.grossProfit   || 0;
  const ta0  = b0.totalAssets   || 1, ta1  = b1.totalAssets   || 1;
  const ca0  = b0.totalCurrentAssets || 0, ca1 = b1.totalCurrentAssets || 0;
  const ppe0 = b0.propertyPlantEquipmentNet || 0, ppe1 = b1.propertyPlantEquipmentNet || 0;
  const dep0 = Math.abs(i0.depreciationAndAmortization || i0.depreciation || 0);
  const dep1 = Math.abs(i1.depreciationAndAmortization || i1.depreciation || 0);
  const sga0 = Math.abs(i0.sellingGeneralAndAdministrativeExpenses || i0.generalAndAdministrativeExpenses || 0);
  const sga1 = Math.abs(i1.sellingGeneralAndAdministrativeExpenses || i1.generalAndAdministrativeExpenses || 0);
  const ltd0 = (b0.longTermDebt || 0) + (b0.totalCurrentLiabilities || 0);
  const ltd1 = (b1.longTermDebt || 0) + (b1.totalCurrentLiabilities || 0);
  const ocf1 = c1.operatingCashFlow ?? c1.netCashProvidedByOperatingActivities ?? 0;
  const ni1  = i1.netIncome || 0;

  const dsri = safe(rec1 / Math.max(r1, 1), rec0 / Math.max(r0, 1));
  const gmi  = safe(gp0  / Math.max(r0, 1),  gp1  / Math.max(r1, 1));
  const aqi  = safe(1 - (ca1 + ppe1) / ta1, 1 - (ca0 + ppe0) / ta0);
  const sgi  = r0 > 0 ? r1 / r0 : null;
  const drr0 = dep0 > 0 ? dep0 / Math.max(dep0 + ppe0, 1) : null;
  const drr1 = dep1 > 0 ? dep1 / Math.max(dep1 + ppe1, 1) : null;
  const depi = (drr0 != null && drr1 != null) ? safe(drr0, drr1) : null;
  const sgai = safe(sga1 / Math.max(r1, 1), sga0 / Math.max(r0, 1));
  const lvgi = safe(ltd1 / ta1, ltd0 / ta0);
  const tata = (ni1 - ocf1) / ta1;

  const vars = { dsri, gmi, aqi, sgi, depi, sgai, lvgi };
  if (Object.values(vars).some(v => v == null || !isFinite(v))) return null;

  const mScore =
    -4.840
    + 0.920 * dsri
    + 0.528 * gmi
    + 0.404 * aqi
    + 0.892 * sgi
    + 0.115 * depi
    - 0.172 * sgai
    + 4.679 * tata
    - 0.327 * lvgi;

  const flag          = mScore > -1.78 ? 'MANIPULATION_RISK' : 'CLEAN';
  const interpretation =
    mScore > -1.78
      ? `M-Score ${mScore.toFixed(2)} (above -1.78 threshold) — potential earnings manipulation; treat GAAP earnings with scepticism`
      : `M-Score ${mScore.toFixed(2)} (below -1.78 threshold) — no manipulation signal detected`;

  return { score: +mScore.toFixed(3), threshold: -1.78, flag, interpretation };
}

// ── Accrual Ratio (Sloan, 1996) ───────────────────────────────────────────────
// One of most replicated findings in accounting research.
// High accruals → GAAP earnings exceed cash earnings → future disappointment risk.
function calcAccrualRatio(is, bs, cf) {
  if (!is || !is.length || !bs || bs.length < 2 || !cf || !cf.length) return null;
  const [i1] = is;
  const [b1, b0] = bs;
  const [c1] = cf;

  const ni  = i1.netIncome || 0;
  const ocf = c1.operatingCashFlow ?? c1.netCashProvidedByOperatingActivities ?? 0;
  const avgAssets = ((b1.totalAssets || 0) + (b0.totalAssets || 0)) / 2;
  if (!avgAssets) return null;

  const ratio = (ni - ocf) / avgAssets;
  const pct   = +(ratio * 100).toFixed(2);
  const flag  = ratio > 0.05 ? 'HIGH_ACCRUALS' : ratio < -0.05 ? 'CONSERVATIVE' : 'NORMAL';
  const interpretation =
    flag === 'HIGH_ACCRUALS'
      ? `Accrual ratio ${pct}% — earnings heavily accrual-based; cash earnings trail GAAP; historically associated with future underperformance`
      : flag === 'CONSERVATIVE'
      ? `Accrual ratio ${pct}% — conservative accounting; cash earnings exceed GAAP; quality earnings signal`
      : `Accrual ratio ${pct}% — normal range; GAAP and cash earnings reasonably aligned`;

  return { ratio: +ratio.toFixed(4), pct, flag, interpretation };
}

// ── Altman Z-Score (simplified) ───────────────────────────────────────────────
// Bankruptcy prediction model. Z < 1.81 = distress zone, Z > 2.99 = safe zone.
function calcAltmanZ(is, bs) {
  if (!is || !is.length || !bs || !bs.length) return null;
  const [i1] = is;
  const [b1] = bs;

  const ta = b1.totalAssets || 1;
  const wc = (b1.totalCurrentAssets || 0) - (b1.totalCurrentLiabilities || 0);
  const re = b1.retainedEarnings || 0;
  const ebit = (i1.operatingIncome || i1.ebitda || 0);
  const bve  = (b1.totalStockholdersEquity || b1.stockholdersEquity || 0);
  const tl   = (b1.totalLiabilities || 0);
  const rev  = (i1.revenue || 0);
  if (!tl) return null;

  // Original Altman Z': uses book equity / total liabilities (for non-public or private proxy)
  const z =
    1.2 * (wc / ta) +
    1.4 * (re / ta) +
    3.3 * (ebit / ta) +
    0.6 * (bve / tl) +
    1.0 * (rev / ta);

  const zone =
    z > 2.99 ? 'safe' :
    z > 1.81 ? 'grey' : 'distress';

  const interpretation =
    zone === 'safe'
      ? `Z-Score ${z.toFixed(2)} (>2.99 safe zone) — low bankruptcy risk`
      : zone === 'grey'
      ? `Z-Score ${z.toFixed(2)} (1.81–2.99 grey zone) — elevated monitoring warranted`
      : `Z-Score ${z.toFixed(2)} (<1.81 distress zone) — elevated bankruptcy risk; strong caution`;

  return { score: +z.toFixed(3), zone, interpretation };
}

// ── Handler ───────────────────────────────────────────────────────────────────

export default async function handler(req) {
  const url = new URL(req.url);
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

  const sym = (url.searchParams.get('sym') || '').toUpperCase().trim();
  if (!sym) return new Response(JSON.stringify({ error: 'sym required' }), { status: 400, headers: CORS });

  if (!FMP_KEY) {
    return new Response(
      JSON.stringify({ available: false, reason: 'FMP_API_KEY not set — add a free key from financialmodelingprep.com' }),
      { status: 200, headers: CORS }
    );
  }

  try {
    const [is, bs, cf] = await Promise.all([
      fmpFetch(`/income-statement/${sym}?limit=2`),
      fmpFetch(`/balance-sheet-statement/${sym}?limit=2`),
      fmpFetch(`/cash-flow-statement/${sym}?limit=2`),
    ]);

    if (!is || !bs || !cf) {
      return new Response(
        JSON.stringify({ available: false, reason: 'Financial statements not found for this symbol' }),
        { status: 200, headers: CORS }
      );
    }

    const piotroski    = calcPiotroski(is, bs, cf);
    const beneish      = calcBeneish(is, bs, cf);
    const accrualRatio = calcAccrualRatio(is, bs, cf);
    const altmanZ      = calcAltmanZ(is, bs);

    // Aggregate quality flags for AI system prompt injection
    const flags = [];
    if (piotroski) {
      if (piotroski.score >= 7) flags.push(`✅ Strong F-Score (${piotroski.score}/9): high-quality fundamentals — historically outperforms`);
      else if (piotroski.score <= 3) flags.push(`🚩 Weak F-Score (${piotroski.score}/9): multiple fundamental red flags — exercise caution on bullish bets`);
    }
    if (beneish?.flag === 'MANIPULATION_RISK') {
      flags.push(`🚨 Beneish M-Score ${beneish.score}: earnings manipulation risk — treat headline EPS with scepticism`);
    }
    if (accrualRatio?.flag === 'HIGH_ACCRUALS') {
      flags.push(`⚠️ High accrual ratio (${accrualRatio.pct}%): GAAP earnings significantly exceed cash earnings — quality concern`);
    }
    if (altmanZ?.zone === 'distress') {
      flags.push(`🚨 Altman Z-Score ${altmanZ.score}: distress zone — elevated bankruptcy risk, avoid unless thesis is recovery play`);
    } else if (altmanZ?.zone === 'grey') {
      flags.push(`⚠️ Altman Z-Score ${altmanZ.score}: grey zone — monitor financial health`);
    }

    // Period covered
    const period = is[0]?.date ? `Based on ${is[0].date} annual filing` : 'Annual filing';

    return new Response(JSON.stringify({
      available:    true,
      period,
      piotroski,
      beneish,
      accrual_ratio: accrualRatio,
      altman_z:     altmanZ,
      quality_flags: flags,
    }), { status: 200, headers: CORS });

  } catch (e) {
    return new Response(
      JSON.stringify({ available: false, reason: e.message }),
      { status: 200, headers: CORS }
    );
  }
}
