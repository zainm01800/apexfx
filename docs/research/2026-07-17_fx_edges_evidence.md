# FX Trading Edges: What's Real, At What Horizon, At What Cost — Sourced Research Report

**Scope note on all numbers below:** academic FX results are computed on institutional instruments (1-month forwards, futures) at month-end prices, mostly *gross* or net of *institutional* costs. Retail MT4 execution (1-pip majors spreads, marked-up swaps) is strictly worse. I flag this per edge. Where evidence is mixed or decayed, I say so.

---

## 1. Currency carry (interest-rate differential)

**Verdict: real premium, crash-prone, and mostly NOT capturable at retail swap rates as a standalone strategy.**

Documented characteristics (G10, 1976–2013): the classic equal-weighted carry returned **~4.0%/yr with Sharpe 0.78**; spread-weighted versions reached **6.6%/yr, Sharpe ~1.02**; all variants have **statistically significant negative skewness** (down to −0.89) and excess kurtosis. Source: [Daniel, Hodrick & Lu, "The Carry Trade: Risks and Drawdowns," NBER WP 20433 / Critical Finance Review 2017](https://www.nber.org/system/files/working_papers/w20433/w20433.pdf). Note their dollar-neutral variant drops to Sharpe **0.49** — much of naive-carry profit is dollar exposure, not the differential itself.

Crash risk is structural, not incidental: carry returns are negatively skewed because of sudden unwinds when funding liquidity dries up ([Brunnermeier, Nagel & Pedersen, "Carry Trades and Currency Crashes," NBER Macro Annual 2008](https://ideas.repec.org/p/nbr/nberwo/14473.html)). High-rate currencies deliver low returns precisely when global FX volatility spikes; a vol-risk proxy explains **>90%** of the cross-section of carry portfolio returns ([Menkhoff, Sarno, Schmeling & Schrimpf, J. Finance 2012](https://ideas.repec.org/a/bla/jfinan/v67y2012i2p681-718.html)). The three biggest diversified-carry drawdowns — 1972–75, 1980–82, **Aug 2008–Feb 2009** — coincide with global recessions/liquidity crises; cross-asset carry Sharpe is ~0.8 per asset class, 1.2 diversified, and survives realistic institutional costs ([Koijen, Moskowitz, Pedersen & Vrugt, "Carry," JFE 2018](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2019/04/Carry.pdf)).

**Retail viability: poor.** Even institutionally, with realistic microstructure frictions the *marginal* Sharpe of currency speculation "can be zero even though the average Sharpe is positive" ([Burnside, Eichenbaum, Kleshchelski & Rebelo, NBER WP 12489](https://ideas.repec.org/p/nbr/nberwo/12489.html)). Retail brokers mark up the tom-next swap: e.g., a top-tier retail broker charges tom-next **+0.6%/yr** on FX ([practitioner audit of Pepperstone](https://bellsforex.com/brokers/pepperstone-market-review.html)); others add **+2%/yr or more** to the benchmark ([LYNX/IBKR schedule](https://tradingfinder.com/brokers/lynx/)). A 2–3%/yr differential (typical among the 22 majors crosses) is largely or fully consumed by the markup, before the negative-skew tail. Verified use for carry in a retail system: **as a directional tilt/filter on trend positions** — carry-with-trend-overlay produced Sharpe ~1 with *lower* drawdown and no negative skew in a 39-currency study ([Clare, Seaton, Smith & Thomas, Univ. of York DP 15/07](https://www.york.ac.uk/media/economics/documents/discussionpapers/2015/1507.pdf)).

- Frequency: monthly rebalance; holding months. Return: 2–7%/yr institutional, ~0–2%/yr net at retail swaps. Max DD: large, multi-year (2008–09 episode; DHL document deep, slow drawdowns).

## 2. Time-series momentum / trend-following — the deepest evidence base

**Verdict: the single best-documented edge in FX. But the evidence lives at monthly formation from daily data — holding periods of weeks to months — NOT at sub-daily horizons.**

- The original TSMOM result: 12-month lookback positively predicts next-month returns for **every one of 58 futures/forward contracts** including currencies; the effect "persists for about a year and then partially reverses" ([Moskowitz, Ooi & Pedersen, "Time Series Momentum," JFE 2012, AQR page](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)).
- 137-year out-of-sample confirmation: an equal-weighted 1/3/12-month trend strategy across 67 markets (12 currency pairs), vol-targeted at 10%, was **profitable in every decade 1880–2016**, net of estimated costs and even 2/20 fees; positive in 8 of the 10 worst 60/40 drawdowns ("crisis alpha"); worst drawdowns ~25% ([Hurst, Ooi & Pedersen, "A Century of Evidence on Trend-Following Investing," JPM 2017](https://fairmodel.econ.yale.edu/ec439/hurst.pdf)).
- Independent replication over two centuries: trend t-stat ≈5 since 1960, ≈10 since 1800 — and critically for your timeframe question: **"no sign of statistical degradation of long trends, whereas shorter trends have significantly withered"** ([Lempérière, Deremble, Potters & Bouchaud, arXiv 2014](https://arxiv.org/abs/1404.3274)).
- FX-specific: simple 4–12-month moving-average trend rules on 39 currencies give Sharpe comparable to carry (~0.6–1.0 depending on construction), **without carry's negative skewness or max drawdown**; combined carry+trend approaches Sharpe ~1 ([Clare et al., York DP 15/07](https://www.york.ac.uk/media/economics/documents/discussionpapers/2015/1507.pdf)).
- Live, fee-deducted track record: the Barclay BTOP50 (mostly trend CTAs) returned **7.0%/yr since 1987 with 9.6% vol and −16% max DD**, ~zero correlation to stocks/bonds ([Man Institute analysis, Jan 1987–Mar 2023](https://www.man.com/maninstitute/trend-following-what-not-to-like)); +15% in 2022 ([Gama/BTOP50 history](https://gamainvestimentos.com.br/views-from-the-floor-history-shows-what-to-expect-from-trend-following-when-rates-are-being-cut/)), +13% for the SG CTA index in 2008 ([AIMA](https://www.aima.org/static/uploaded/06348e9a-035b-42d3-954e5ea4c9498d20.pdf)). **Honest downside: a lost decade** — SG CTA returned 0.03%, −2.87%, +2.48%, −5.84% in 2015–2018, the longest flat stretch on record ([Diversifying Trends, ScienceDirect](https://www.sciencedirect.com/science/article/pii/S245230622100109X)).
- Older caveat worth heeding: *simple* daily MA rules on the major pairs stopped being profitable after the 1990s (Olson 2004; Pukthuanthong-Le/Levich/Thomas 2007; Neely/Weller/Ulrich 2009 — all cited in the [BIS momentum survey, WP 366, p.8](https://www.bis.org/publ/work366.pdf)). What survives is diversified, vol-scaled, multi-market trend — not single-pair crossover rules.

**Sub-daily trend:** thin. The one robust intraday effect is "rest-of-day return predicts the last-30-minute return," found across 60+ futures incl. **8 currency futures, 1974–2020**, gross Sharpe 0.87–1.73 by asset class — but it's one trade per day at the close, reverts within 3 days, and the currency-futures leg is the weakest ([Baltussen, Da, Lammers & Martens, JFE 2021](https://www3.nd.edu/~zda/intramom.pdf)). FX-specific intraday momentum exists in one EM pair (RUB/USD) and is driven by dealer overnight-risk aversion ([Elaut, Frömmel & Lampaert, J. Financial Markets 2018](https://ideas.repec.org/a/eee/finmar/v37y2018icp35-51.html)). **There is no peer-reviewed evidence for 15m/1h FX momentum surviving retail spreads.** Nothing credible supports scalping-timeframe momentum.

## 3. Cross-sectional currency momentum

**Verdict: real gross, partially cost-explained, unstable — usable at monthly frequency on 22 pairs.**

Up to **10%/yr** spread between past-winner and past-loser currency portfolios (48 currencies, 1976–2010, formation 1–12 months, holding 1–12 months); **not** explained by standard risk factors, **partially** explained by transaction costs, skewed toward high-cost currencies, with "very effective limits to arbitrage" and returns unstable over short windows; winner-loser spread reverses beyond ~12–36 months ([Menkhoff, Sarno, Schmeling & Schrimpf, "Currency Momentum Strategies," JFE 2012 / BIS WP 366](https://www.bis.org/publ/work366.pdf)). Earlier, simpler version on 8 majors: ~6%/yr (Okunev & White 2003, cited therein). Capacity is a non-issue at £100k; the binding constraints are cost control (monthly, not weekly, rotation) and tolerance for multi-month flat/losing streaks.

## 4. Currency value (PPP-based)

**Verdict: real but slow — a strategic tilt, not a tradable signal on 15m/1h/1d.**

5-year real-exchange-rate changes predict the cross-section of currency returns: raw PPP-value Sharpe **0.44–0.51**; purged of macro fundamentals (productivity, export quality, net foreign assets, output gap) Sharpe rises to **0.8–0.9**, mostly via lower volatility; predictability extends 1–2 years out; **quarterly** rebalancing; largely uncorrelated with carry and momentum ([Menkhoff, Sarno, Schmeling & Schrimpf, "Currency Value," RFS 2017](https://openaccess.city.ac.uk/id/eprint/14851/13/FXVALUE_Rev3_cepr.pdf)). Value+momentum in currencies also appears in [Asness, Moskowitz & Pedersen, "Value and Momentum Everywhere," J. Finance 2013](https://doi.org/10.1111/jofi.12021). For a retail 22-pair system: use PPP deviation only as a slow regime variable (e.g., cap contrarian-to-value positions), never as an entry trigger.

## 5. Mean reversion — mostly noise, two narrow exceptions

**Verdict: no credible evidence for general daily/weekly reversal trading in FX. Where it "works," it's either a PPP/value effect (section 4) or a narrow calendar effect.**

- The defensible academic "mean reversion" result is reversion toward **UIP/carry-implied levels**, i.e., a slow fundamental anchor: Serban's combined momentum+mean-reversion strategy outperformed both carry and MA rules — but the reversion leg operates at monthly horizons on UIP deviations, not daily price dips ([Serban, J. Banking & Finance 2010](https://econpapers.repec.org/RePEc:eee:jbfina:v:34:y:2010:i:11:p:2720-2727)).
- One narrow, cost-robust calendar effect: **weekend-gap reversal** — after a large Friday-close→Monday-open gap, spot rates tend to reverse during the following week, robust to transaction costs in out-of-sample tests on 17 currencies ([Dao, McGroarty & Urquhart, J. Multinational Financial Management 2016](https://ideas.repec.org/a/eee/mulfin/v37-38y2016ip158-167.html)). Single study, small capacity of conviction — treat as speculative.
- Daily/intraday reversal in liquid majors is essentially bid-ask bounce and liquidity-provision effects (cf. the reversal findings inside [Baltussen et al. 2021](https://www3.nd.edu/~zda/intramom.pdf) — last-30-min moves *revert* over the next 3 days). Note who the counterparty is for short-horizon reversal: retail traders themselves are systematic contrarians who fade moves and cannot extract fundamental information from news — and simple strategies *against* retail flow are profitable ([Kaourma et al., J. Int. Financial Markets, Institutions & Money 2025](https://www.sciencedirect.com/science/article/abs/pii/S1042443125000368)). Running short-horizon reversal at retail spreads puts you on the losing side of that finding.

## 6. Volatility / seasonality / news effects

These are **risk-management edges (Sharpe improvers), not standalone alpha** — and they're the most directly implementable items in this report for a regime-gated system:

- **Vol scaling / vol regimes:** scaling exposure down when realized vol is high raises Sharpe for the market, momentum, and the *currency carry trade* ([Moreira & Muir, "Volatility-Managed Portfolios," J. Finance 2017](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12513)). Carry losses concentrate in vol spikes ([Menkhoff et al. 2012](https://ideas.repec.org/a/bla/jfinan/v67y2012i2p681-718.html)); trend itself is vol-targeted at ~10% in the AQR evidence. Your vol-scaling and regime gate are aligned with the literature — the problem is the timeframe, not the concept.
- **Volatility risk premia** predict exchange rates (option-implied minus realized vol; Sharpe-generating long/short portfolios), stronger than carry and momentum spot predictability — but requires FX options data; usable only as a regime input ([Della Corte, Ramadorai & Sarno, CEPR DP 9549 / JFE 2016](https://ideas.repec.org/p/cpr/ceprdp/9549.html)).
- **News windows:** scheduled macro releases drive most time-of-day and day-of-week volatility patterns in FX futures; price adjustment to major news happens **within ~1 minute**, with vol elevated ~15 minutes and slightly for hours ([Ederington & Lee, J. Finance 1993](https://ideas.repec.org/a/bla/jfinan/v48y1993i4p1161-91.html)). Practical rule: no new entries and wider/no stops across red-flag releases — execution hygiene, not signal.
- **Intraday/weekly vol seasonality** is stable and geographically structured (London/NY/Tokyo handoffs): [Dacorogna et al., JIMF 1993](https://ideas.repec.org/a/eee/jimfin/v12y1993i4p413-438.html) — useful for execution timing and vol normalization, again not alpha.

## 7. Per-edge summary

| Edge | Documented return / Sharpe / DD (institutional, mostly gross) | Frequency | Survives ~1 pip retail costs? |
|---|---|---|---|
| Carry (naive) | 4–7%/yr; SR 0.5–1.0; neg. skew; deep multi-month DDs (DHL; Koijen) | Monthly, hold months | **No** — retail swap markup 0.6–3%/yr eats the differential |
| Carry as trend filter | carry+trend SR ~1, lower DD, no neg. skew (Clare et al.) | Monthly | Yes (costs nothing extra) |
| TS trend (1/3/12-mo) | Positive every decade since 1880; BTOP50 live 7%/yr, SR~0.4 net of fees, −16% DD; flat 2011–19 | Daily signals, hold weeks–months | **Yes** — few RTs/month |
| XS momentum | up to 10%/yr gross; partially cost-explained (Menkhoff) | Monthly rotation | Marginal-to-yes on majors at monthly freq |
| Value (PPP) | SR 0.5 raw / 0.8–0.9 adjusted; hold quarters–years (Menkhoff RFS) | Quarterly | Yes (tiny turnover), but slow |
| Mean reversion | Only UIP-level (monthly) + weekend-gap (weekly, single study) | Monthly / weekly | Daily/hourly reversal: **no evidence, costs kill it** |
| Vol scaling / news filters | Sharpe improvement, not standalone return (Moreira-Muir; Ederington-Lee) | Continuous | Yes — pure risk management |
| Intraday last-30-min momentum | SR 0.87–1.73 gross across futures; weakest for FX; reverts in 3 days (Baltussen) | 1 trade/day at close | Fragile — needs near-zero cost; majors only |

## 8. Ranked shortlist for a 22-pair, retail-cost, MT4 system

1. **Vol-scaled time-series trend on daily/weekly bars, signals from 1/3/12-month lookbacks, positions held weeks–months.** Strongest and most replicated evidence (Moskowitz 2012; Hurst 2017; Lempérière 2014; Clare 2015; 37-year live BTOP50 record). Expect SR ~0.3–0.6 net, multi-month drawdowns, and flat years — not a high-Sharpe machine. This is the evidence-based home of your existing momentum core.
2. **Volatility management as a first-class layer** (already your instinct): vol-target sizing plus cutting exposure when short-run realized vol spikes (Moreira-Muir; Menkhoff 2012 carry-vol). Highest certainty-per-unit-effort improvement available.
3. **Carry strictly as a filter/tilt** (only take trend signals whose direction earns positive carry; penalize anti-carry trades). Free to implement, documented to raise Sharpe and cut skew (Clare et al.; Jordà & Taylor via DHL). Do not chase swap income directly.
4. **Cross-sectional momentum overlay, monthly** rebalance across your 22 pairs (long top-third, short bottom-third by 1–3-month returns) — complements TS trend; keep turnover monthly to survive costs (Menkhoff 2012; Okunev-White).
5. **News/vol calendar gates**: flat across top-tier releases, no entries in the first ~15 min after them; skip dead sessions (Ederington-Lee; Dacorogna).
6. **PPP-value as a slow regime bound** (optional): cap position size when a trade fights a large 5-year real-exchange-rate deviation (Menkhoff RFS 2017).

**Drop per the evidence:** 15m/1h momentum or reversal as a primary signal (no literature support; short trends have "significantly withered" — Lempérière; simple daily rules died in majors decades ago — Levich/Neely lineage); naive swap-harvesting carry (Burnside's zero marginal Sharpe + retail swap markup); short-horizon mean-reversion scalping (you'd be the contrarian retail flow that Kaourma et al. show is profitably faded); exotic/wide-spread pairs for anything high-turnover. Sobering base rate: **74–89% of retail FX/CFD accounts lose money** in regulator-mandated broker disclosures ([summary of ESMA/FCA disclosures](https://pomegra.io/learn/library/track-d-other-assets/forex/chapter-11-why-retail-forex-trading-is-brutal/retail-forex-loss-statistics)).

## 9. Caveats

- I could not verify a specific "Della Corte & Potì" FX momentum paper — the closest verified trend/carry work is Clare-Seaton-Smith-Thomas (York) and Della Corte-Sarno on volatility premia; treat the original reference cautiously.
- Almost all cited Sharpes are pre-retail-cost; at 1-pip majors spreads (≈9 bps round trip) a daily-bar trend system pays ~0.5–1.5%/yr, a 15m system with 2 RTs/day pays ~45%/yr. That arithmetic, not signal quality, is what decides retail viability.
- Several practitioner sources (broker swap audits, loss-rate summaries) are flagged as such; the peer-reviewed anchors are the ones to weight.
