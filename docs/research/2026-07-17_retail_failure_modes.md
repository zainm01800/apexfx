# Why Retail Algorithmic Trading Systems Fail When They Go Live — and What Demonstrably Improves the Odds

**Incident context this research is meant to inform:** an unvalidated signal went live with a config mistake that allowed ~19× leverage; 87 trades produced −1.6% of account at a 36.8% win rate. Every finding below maps to one or more of those three failure modes. Evidence-quality flags are given inline: **[strong]** = academic/regulator/primary; **[medium]** = industry data, practitioner measurement; **[weak]** = blog/forum, included only where better sources don't exist.

---

## 1. Documented retail trader performance — the base rates are brutal

**Regulator-mandated disclosures and regulator studies:**

- **ESMA (EU):** national regulators' account-level data showed **74–89% of retail CFD accounts lose money, with average losses per client of €1,600–€29,000**. This was the stated basis for the 2018 intervention (leverage caps of 30:1 on major FX pairs, margin close-out, negative balance protection, bonus ban). [strong] ([ESMA press release ESMA71-98-128, 27 March 2018](https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors))
- **AMF (France):** all 14,799 retail clients trading Forex/CFDs at the four largest authorized brokers, 2009–2013: **89% lost money; mean result −€10,887; median −€1,843; aggregate −€161M over 16.2M trades**. Only 121 of 14,799 made more than €24,000 over four years; 722 lost more than €50,000. [strong] ([AMF, Lettre de l'Observatoire de l'épargne n°10, Oct 2014, PDF](https://www.amf-france.org/sites/institutionnel/files/contenu_simple/lettre_ou_cahier/lettre_observatoire/La%20lettre%20de%20l'Observatoire%20de%20l'epargne%20de%20l'AMF%20-%20ndeg%2010%20-%20Octobre%202014.pdf); [summary](https://www.capital.fr/entreprises-marches/marche-forex-neuf-investisseurs-sur-dix-perdent-leur-culotte-967979))
- **FCA (UK):** firm-reported data put UK CFD client loss rates at **82% (2015 sample) and 78% (Aug–Oct 2017 sample in CP18/38)**. [strong for the paper, secondary for the split] ([FCA CP18/38 PDF](https://www.fca.org.uk/publication/consultation/cp18-38.pdf); [breakdown](https://www.theinvestorscentre.co.uk/trading/statistics/cfd-trading/))
- **ASIC (Australia):** in FY2024 — *after* leverage caps had been in force for years — **68% of retail CFD investors still lost money, totalling >A$458M including A$73M in fees**. Regulation caps the damage; it does not create edge. [strong] ([ASIC media release 26-004MR / Report 828](https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2026-releases/26-004mr-asic-secures-nearly-40-million-in-refunds-to-investors-and-drives-change-after-cfd-sector-falls-short/))
- **US (NFA/CFTC regime):** broker quarterly profitability reports show ~**36% of accounts profitable in any given quarter** (Q1 2015 weighted average) — but this is a quarterly snapshot that overstates long-run results, since losers churn out and the same accounts are not tracked across quarters. [medium] ([Finance Magnates Q1 2015 US profitability report](https://www.financemagnates.com/forex/brokers/exclusive-us-q1-2015-forex-profitability-report-more-accounts-and-profits/))

**Academic account-level studies:**

- **Brazil (Chague, De-Losso & Giovannetti 2020)** — every individual who began day trading equity futures 2013–2015 and persisted ≥300 trading days: **97% lost money; only 1.1% earned more than Brazilian minimum wage; 0.4% more than a bank teller (~US$54/day); the single best individual averaged US$310/day with a daily standard deviation of US$2,560. No evidence of learning with experience.** The authors conclude it is "virtually impossible for individuals to compete with HFTs and day trade for a living." [strong] ([SSRN 3423101](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101); [QuantPedia summary](https://quantpedia.com/retail-day-trading-is-an-uphill-battle/))
- **Taiwan (Barber, Lee, Liu & Odean 2014)** — all day traders 1992–2006: **less than 1% of day traders predictably and reliably earn positive abnormal returns net of fees**; the top 500 repeat-performers earned 37.9 bps/day after fees, while bottom-ranked traders lost −28.9 bps/day after fees. Skill exists but is vanishingly rare, and the winners are the ones with speed, information and scale advantages — not retail. [strong] ([Journal of Financial Markets 18:1–24 via RePEc](https://ideas.repec.org/a/eee/finmar/v18y2014icp1-24.html); [author PDF, Berkeley](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf))
- **US households (Barber & Odean 2000)** — 66,465 discount-broker households 1991–96: average household netted 16.4%/yr vs the market's 17.9%, and **the most active quintile netted just 11.4%/yr — a ~6.5pp annual penalty for trading frequently**. The least active quintile earned 18.5%. [strong] ([Journal of Finance 55(2), author PDF](https://faculty.haas.berkeley.edu/odean/papers%20current%20versions/individual_investor_performance_final.pdf))

**Takeaway for this system:** a 36.8% win rate and −1.6% after 87 live trades is not a surprising outcome needing explanation — it is the modal outcome. The burden of proof is entirely on the strategy to demonstrate it's in the ~1–3% tail, and 87 trades cannot do that (standard error of the win-rate estimate at n=87 is ±5pp).

---

## 2. Backtest overfitting — the rigorous literature

This is the most directly relevant literature to "backtest looked great, went live, lost."

- **Bailey, Ger, Lopez de Prado, Sim & Wu, "Statistical Overfitting and Backtest Performance"** — their headline result: **with only 5 years of daily data, if 45 or more independent strategy variants are tried, the best variant will more likely than not show a Sharpe ≥ 1.0 even when generated on pure random walk data**. Their online simulator (SEBO) optimizing just 2–3 parameters over ~55,000 combinations produced in-sample Sharpe ≈ 1.59 that collapsed to −0.18 out-of-sample; across 400 runs on Gaussian noise, in-sample Sharpe centered on **+0.9** while out-of-sample centered on **zero**. They also show the common "hold-out" fix is unreliable — with enough attempts you find variants that pass both IS and OOS yet have no real edge. [strong] ([LBL paper PDF](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf))
- **Bailey, Borwein, Lopez de Prado & Zhu, "The Probability of Backtest Overfitting" (J. Computational Finance 2017)** — introduces **PBO** via **combinatorially symmetric cross-validation (CSCV/CPCV)**: estimate the probability that the strategy you selected will underperform the median of all trials out-of-sample. Standard CV methods (hold-out) "tend to be unreliable and inaccurate in the context of investment backtests." [strong] ([SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253))
- **Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (JPM 2014)** — **DSR** discounts a reported Sharpe for the number of trials run, plus skew/kurtosis: "not controlling for the number of trials involved in a particular discovery leads to over-optimistic performance expectations." Companion concepts: **Minimum Track Record Length / Minimum Backtest Length**. [strong] ([SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551); SSRN 1821643 for MinTRL is referenced within)
- **Harvey, Liu & Zhu, "…and the Cross-Section of Expected Returns" (RFS 2016)** — 316 published factors re-tested under multiple-testing corrections: **a new factor needs a t-statistic above ~3.0, not 2.0**; "most claimed research findings in financial economics are likely false." [strong] ([NBER w20592 PDF](https://www.nber.org/system/files/working_papers/w20592/w20592.pdf))
- **Harvey & Liu, "Backtesting" (JPM 2015)** — the **haircut Sharpe ratio**: the Sharpe you report should be deflated by the number of independent trials you (or anyone) ran on the same data. [strong] ([published paper, Duke](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF))
- **Lopez de Prado, "The 10 Reasons Most Machine Learning Funds Fail" (JPM 2018)** — the canonical failure list: working in silos, backtest overfitting as #1 operational sin, wrong labeling/sampling/weighting, and publishing backtests rather than paper-trading first. [strong] ([SSRN 3104816; GARP-hosted PDF](https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf))

**Practical rules of thumb derived from this literature:**

1. **Trial budget:** the number of variants you've ever tried on a dataset is the denominator of your credibility. ~45 trials on 5 years of daily data and your "best" Sharpe of 1.0 is *expected by chance alone*. (Bailey et al. above)
2. **Minimum sample size:** a Sharpe estimate's t-stat is roughly SR×√T. To distinguish a true annualized Sharpe of 1.0 from zero at 95% confidence you need ~4 years of daily returns; for Sharpe 0.5, ~15 years. (Standard SR inference, formalized in the DSR/MinTRL papers above)
3. **Expect ≥50% IS→OOS Sharpe degradation**, and discount the backtest Sharpe explicitly for trials (DSR/haircut) before sizing anything.
4. **An untouched out-of-sample period plus live paper-trading is mandatory** — and even that fails if you iterate against it (Bailey et al.'s hold-out result).

---

## 3. Backtest→live divergence in FX specifically

**Costs most retail backtests understate:**

- **Spread:** retail EUR/USD reality in 2024–25: ~0.19 pip average raw + commission ≈ 0.59 pip all-in at Interactive Brokers; ~0.61 pip typical at CMC standard accounts; 0.8–1.2 pips at typical retail/FTMO-style accounts. [medium] ([ForexBrokers.com comparison data](https://www.forexbrokers.com/compare/cmc-markets-vs-interactive-brokers); [FTMO-average figures via JPTradingCapital](https://www.jptradingcapital.com/blog/en/prop-firm-trading-bot))
- **Slippage & latency:** MT4's Strategy Tester assumes instant, frictionless fills at known prices; retail live execution carries ~50–300 ms latency and price-dependent fills, which is lethal for strategies targeting a few pips. [weak-medium — practitioner measurement, flagged] ([MT4 Strategy Tester vs live analysis](https://mt4programming.com/beyond-the-90-myth-why-mt4-strategy-tester-results-fail-in-live-markets-and-how-to-fix-it/); [demo-vs-live comparison table](https://forexairobot.com/best-scalping-ea-mt4/))
- **Demo vs live on MT4 market execution:** demo fills at displayed price with no slippage/requotes; live fills depend on available liquidity, and spreads can differ between demo and live feeds. [weak-medium, practitioner consensus — flagged] ([comparison](https://www.forexairobot.com/best-scalping-ea-mt4/); [ForexFactory thread](https://www.forexfactory.com/thread/297306-how-do-demo-accounts-differ-from-real-live))
- **Spread widening exactly when it hurts:** FX liquidity is strongly common across pairs and **deteriorates sharply in stressed/volatile markets** — i.e., precisely when stops trigger. [strong] ([Karnaukh, Ranaldo & Söderlind, "Understanding FX Liquidity," RFS 28(11):3073–3108, 2015 — citation record](https://ideas.repec.org/a/oup/rfinst/v28y2015i11p3073-3108..html))
- **Swap/rollover:** positions held past 5pm NY are charged/credited the interest differential; most brokers apply **triple swap on Wednesday** (T+2 settlement over the weekend). For anything held overnight, financing is a real drag that most backtests ignore. [medium — broker documentation, flagged] ([BestBrokers explainer](https://www.bestbrokers.com/education/trading-spreads-commissions-and-costs/); [broker swap mechanics](https://www.tradingplatforms.co.uk/reviews/global-prime/))
- **Gaps / stop-loss failure:** on 15 Jan 2015 the SNB abandoned the EUR/CHF floor; the franc appreciated >41% intraday vs the euro. **Stop orders filled ~1,000+ pips from their triggers on some feeds; retail clients ended with negative balances; FXCM lost $225M and needed a $300M emergency loan; Alpari UK went insolvent; estimated client losses >$400M.** A stop loss is an instruction, not a price. [strong for event facts — Delaware Chancery opinion; medium for the fill-detail figures] ([Brett Kandell v. Dror Niv et al., Court of Chancery](https://law.justia.com/cases/delaware/court-of-chancery/2017/ca-11812-vcg.html); [FTMO recap](https://ftmo.com/en/blog/the-biggest-flash-crash-in-forex-can-a-similar-situation-happen-again/); [practitioner account](https://forexmechanics.com/intermarket-analysis/))

**Why this matters per timeframe — computed on your own data:** I computed ATR(14) for EUR_USD from `engine/data_store` (data current through 2026-07-17):

| Timeframe | ATR(14), mean | ATR(14), median | 1-pip round-trip cost as % of one bar's range |
|---|---|---|---|
| 15m | 5.9 pips | 5.2 pips | **~17%** |
| 1h | 12.9 pips | 11.7 pips | **~8%** |
| 1d | 79.9 pips | 76.0 pips | **~1.3%** |

Credible practitioner cost models for FX backtests: fixed ≥1 pip per side plus slippage (FXGlory's published methodology uses **1.5 pips spread + 0.5 pips slippage per side** on majors), with time-of-day and news multipliers. [medium] ([FXGlory methodology](https://fxglory.com/learn/forex-strategies/forex-zigzag-strategy/); [PineForge slippage modeling](https://getpineforge.com/blog/slippage-commission-trading-bot-costs)) On 15m data, those assumptions routinely consume the entire edge; academic work on intraday FX rules reaches the same conclusion — apparent intraday profitability does not survive realistic costs (Neely & Weller, JIMF 2003, 22(2):223–237 — [citation record](https://ideas.repec.org/a/eee/jimfn/v22y2003i2p223-237.html)), and moving-average-rule FX profits that existed in the 1970s–80s had **declined to roughly zero by the 1990s** (Olson, J. Banking & Finance 28(1):85–105, 2004 — [citation record](https://ideas.repec.org/a/eee/jbfina/v28y2004i1p85-105.html)). More broadly, after data-snooping corrections and transaction costs, technical rules show no persistent outperformance (Bajgrowicz & Scaillet, JFE 106(3):473–491, 2012 — cited in the [PLOS ONE reference list](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0276322)). [all strong]

---

## 4. Position sizing — what the evidence says about survival

- **Full Kelly is a ruin machine in practice.** Thorp's own treatment gives the probability of ever being reduced to a fraction *x* of your initial bankroll; for full-Kelly growth-optimal betting that probability is *x* itself — **a 50% chance of losing half your bankroll at some point**. Full Kelly also assumes perfectly known edge; estimation error makes it systematically overbet. [strong] ([Thorp, "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market," 2006, PDF](https://gwern.net/doc/statistics/decision/2006-thorp.pdf))
- **Fractional Kelly is the professional standard:** half/quarter-Kelly sacrifices growth roughly linearly but cuts drawdown and ruin probability roughly exponentially; empirical portfolio implementations confirm half-Kelly materially reduces drawdowns. [strong/medium] ([Carta & Conversano, Frontiers in Applied Mathematics & Statistics, 2020](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2020.577050/full); [practitioner summary of the growth-vs-ruin tradeoff](https://nexusfi.com/a/risk-management/kelly-criterion))
- **Volatility targeting/scaling:** Moreira & Muir show volatility-managed portfolios raise Sharpe and utility across factors [strong] ([J. Finance 72(4):1611–1644, 2017 — citation record](https://ideas.repec.org/a/eee/jfinec/v149y2023i3p378-406.html)) — **but honest flag:** Cederburg et al. (JFE 2020) find volatility-managed portfolios do *not* systematically outperform out-of-sample in direct comparisons ([JFE 138(1):95–117](https://ideas.repec.org/a/eee/jfinec/v138y2020i1p95-117.html)). Treat vol targeting as drawdown control, not alpha.
- **Regulators priced in the leverage evidence:** ESMA capped retail FX majors at 30:1 because "excessive leverage" was the driver of retail losses (same press release, §1). At the **19× leverage this system ran**, a single EUR/USD daily ATR move (0.7%) against the book ≈ 13% of equity; a 2015-style 30% CHF gap ≈ 570% of equity — account gone plus debt absent negative-balance protection. (Arithmetic on the ATR data above and the SNB event record; labeled as such.)
- **Realistic retail risk-per-trade:** the Kelly math with realistic retail edges (Sharpe ≤ 0.5, win rate ~45–55%, R:R ~1) yields fractions of a percent; the literature-supported ceiling for survival-oriented retail sizing is ~0.25–1% of equity at risk per trade, with portfolio-level "heat" (sum of open risk) capped at ~2–6%. This is a synthesis of fractional-Kelly practice above — presented as such, not as a single paper's finding.

---

## 5. Realistic returns — what professional systematic funds actually make

- **SG CTA Index** (the 20 largest managed-futures managers, net of fees): best year since inception in 2000 was **+20.1% (2022)**; four-year total 2019–2022 +39.8%. [strong] ([SocGen review via AlphaWeek](https://www.alpha-week.com/2022-cta-index-performance-review))
- More recent/typical regimes are far lower: **SG Trend Index ≈ 2.2% annualized Jan 2015–Jun 2025** [medium] ([Quantica Quarterly Insights Q3 2025, PDF](https://quantica-capital.com/publications/pdf/2025Q3_QuanticaQuarterlyInsights.pdf)); **SG CTA Index ≈ 3.2% annualized Mar 2022–Feb 2026** [medium] ([Simplify fund insights](https://www.simplify.us/sites/default/files/fund-insights/2026-03/Simplify-FI-CTA-Four-Years-In.pdf)); a broad trend-following composite produced ~**8.0% CAGR 2000–Sep 2023** with a deep max drawdown along the way [medium] ([Top Traders Unplugged](https://www.toptradersunplugged.com/trend-following-performance-report-september-2023/)).
- Manager dispersion is huge: Winton's white paper shows SG Trend constituents normalized to 10% vol delivered **3.1%–7.7% per annum** long-run, i.e. Sharpe ≈ 0.3–0.8 even for the best. [medium] ([Winton/Alma white paper PDF](https://www.almacapital.com/wp-content/uploads/2019/10/2023-12-Winton-White-Paper-Selecting-a-Trend-Following-CTA.pdf))
- **AQR's century-long trend backtest** — the best-case academic benchmark with institutional execution — delivered ~11%/yr at Sharpe ~0.7 *after estimated transaction costs* across 67 markets, 1880–2016. Note: positive every decade, and this is a *gross-of-fees, capacity-constrained-in-practice* stylized portfolio, not a retail result. [strong] ([Hurst, Ooi & Pedersen, JPM 44(1):15–29, 2017 — paper PDF](https://www.wallstreetcourier.com/wp-content/uploads/data_download/research/A_Century_of_Trend_Following_Investing.pdf); [summary](https://quantdecoded.com/en/trend-following-the-case-for-time-series-momentum))

**Translation to kill the "thousands a month" expectation:** long-run professional systematic Sharpe is **~0.3–0.7 net**. At a sane 10% annual vol target that's **~3–7% per year ≈ 0.25–0.6% per month**. On a $10k account that is $25–60/month. To make "thousands a month" on $10k you need >10%/month — an annualized Sharpe far beyond anything the managed-futures industry has ever sustained — and the only retail mechanism that even *attempts* it is exactly the 19×-style leverage that regulators capped and that produced this incident.

---

## 6. Trade frequency vs edge — the arithmetic of "many trades a day"

- **Frequency is negatively correlated with retail outcomes** (Barber & Odean: most active quintile −6.5pp/yr vs market, §1) and **intraday retail day-trading for a living is near-impossible after costs** (Chague: 97% of persisters lose; "virtually impossible to compete with HFTs," §1).
- **The breakeven math on your own market data.** With symmetric target/stop of R pips and round-trip cost c, breakeven win rate = (R+c)/(2R). Using c = 1 pip (best retail case) and c = 2 pips (1 pip spread + ~1 pip slippage/commission, realistic):

| Style | Typical target R (vs EUR/USD ATR above) | Breakeven WR @ c=1 pip | Breakeven WR @ c=2 pips |
|---|---|---|---|
| Scalp, 15m bars | 5 pips | 60% | **70%** |
| Intraday, 1h bars | 10 pips | 55% | **60%** |
| Intraday, wide | 20 pips | 52.5% | 55% |
| Swing, daily | 50 pips | 51% | 52% |

  (Labeled arithmetic, no external source needed.) A "many trades a day" system on 15m data needs a **sustained 60–70% win rate at 1:1 R:R just to break even** — against counterparties that include the HFTs Chague et al. showed retail cannot beat. Your live 36.8% win rate at high frequency is the expected outcome of trading a cost hurdle that large with an unvalidated signal. Higher timeframes (4h/daily) shrink the cost share to ~1–3% of the available move, which is the only reason retail systematic trading has any defensible niche at all.

---

## 7. The checklist — 12 process rules that would have prevented this incident

Mapped to the three root causes (unvalidated signal live, 19× leverage via config mistake, expectations mismatch):

1. **Research trial ledger + deflated metrics.** Log every backtest variant ever run. Before any live deployment, compute Deflated Sharpe / PBO; reject any strategy whose edge disappears after trial-count deflation. (Bailey et al.; Harvey & Liu)
2. **Statistical minimums before live capital.** Require t = SR×√T ≥ 2 on the *out-of-sample* period (≈4 years daily data for SR 1.0) **and** ≥100 live-equivalent paper trades. 87 live trades proves nothing either way (±5pp on win rate).
3. **Hard quarantine workflow:** in-sample → untouched out-of-sample → ≥3–6 months paper-trading incubation → minimum-size live canary. Never iterate against the OOS or the paper period (hold-out fails if you reuse it — Bailey et al.).
4. **Cost stress in backtest:** model spread + slippage + swap at ≥1×, 2×, and 3× your measured live costs (spread+slippage ≈ 1–2 pips/side on majors at retail). Reject any strategy whose net edge-per-trade isn't at least 2× round-trip cost — which eliminates most sub-1h strategies on retail feeds (see the ATR/breakeven tables).
5. **Fail-closed configuration.** The risk layer must parse config defensively: unknown/missing/garbage sizing parameters → minimum size or halt, *never* maximum. Add an independent hard-coded ceiling (max leverage, max notional per symbol, max concurrent risk) that no config can override. This is the direct fix for the 19× mistake.
6. **Fixed-fractional sizing with hard caps.** ≤0.25–1% of equity at risk per trade (quarter-Kelly discipline), portfolio heat cap ~2–6%, enforced in a separate risk module from the signal code.
7. **Automatic kill-switch.** Flatten and halt at a predefined daily loss (e.g. −2%) and peak-to-trough drawdown (e.g. −8–10%); require human review before restart. (Survival first: Thorp/fractional-Kelly evidence.)
8. **Gap and weekend policy.** Treat stops as instructions, not prices (SNB 2015). No elevated leverage through weekends/news blackouts; stress-test the book against a 5–10× ATR instantaneous gap; use negative-balance-protected brokers only (ESMA-mandated in EU/UK).
9. **Deployment parity and change control.** Config/strategy changes go through review, automated tests, and a canary at minimum size; measure live slippage vs the backtest model continuously and alert when realized cost > model (demo≠live on MT4 market execution).
10. **Expectations governance in writing.** Any return projection must be stated as a Sharpe assumption benchmarked against the SG CTA reality (0.3–0.7 long-run). A plan requiring >1–2%/month sustained is automatically rejected as a leverage plan, not a trading plan.
11. **Pre-registered evaluation.** Before going live, write down: evaluation window, minimum trades, decision thresholds for scale-up/halt. Prevents both premature scaling (this incident) and premature abandonment of possibly-valid strategies.
12. **Track record before trust.** Capital scales with *live verified* Sharpe (target ≥0.5 over ≥100 trades at min size), not with backtest confidence. Backtests set hypotheses; only incubated live evidence allocates capital.

---

## Evidence-quality notes (candor)

- Strong: ESMA, FCA CP18/38, AMF, ASIC primary documents; Chague et al.; Barber & Odean (both studies); Bailey/Lopez de Prado/Harvey/Liu papers; Thorp; Moreira & Muir; Cederburg et al.; Karnaukh et al.; Neely & Weller; Olson; Bajgrowicz & Scaillet; the Delaware Chancery opinion on SNB.
- Medium: ForexBrokers.com spread measurements; Simplify/Quantica/AlphaWeek/Winton/TTU figures for CTA performance (industry-calculated indices; SG indices are net-of-fee composites but not investable); Finance Magnates US quarterly profitability (quarterly snapshot bias); broker swap/triple-Wednesday documentation.
- Weak (flagged, used only for MT4 execution mechanics where no academic source exists): mt4programming.com, forexairobot.com, forexmechanics.com, forum threads. The core claims they support (demo fills lack slippage; tester ignores latency) are consistent with practitioner consensus but not peer-reviewed.
- The ATR table and breakeven-win-rate table are **computed from this repo's own `engine/data_store` EUR_USD parquet files** (through 2026-07-17) and stated formulas — reproducible arithmetic, not external claims.
- One thing I could not verify: the Harvey & Liu "Backtesting" PDF at Duke is image-encoded, so I cite the paper's existence/method rather than quoting specific haircut numbers.
