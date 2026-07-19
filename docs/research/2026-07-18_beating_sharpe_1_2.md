# Deep Dive: Pushing a UK-Retail Spot-Only Book Above Sharpe 1.2 Net

**Scope constraints honoured:** long-only spot (stocks, UCITS ETFs, crypto spot, FX majors), daily/4h bars, £100k–1M, no futures/options/perps. Capacity is a non-issue everywhere except small-cap crypto (still fine at £1M). Every claim has a source; practitioner sources are flagged.

---

## 1. Cross-sectional crypto momentum (universe-level)

**What the literature actually documents:**

- Short-horizon cross-sectional momentum (1–4 week lookback → 1-week hold) is statistically significant in crypto: [Dobrynskaya (HSE)](https://conference.hse.ru/files/download_file_ex?hash=FAE0AB2DC7A67656E89A0B1CB27D8C7D&id=3B5EE9A5-0B18-458A-9458-B4ED0F6C6664) documents "significant but rather small momentum at 2–4 weeks that turns into a significant and economically large reversal at longer horizons (up to 2 years)," with reversal driven by past losers. [Impact of size and volume on cryptocurrency momentum and reversal (2023)](https://quantitative.cz/wp-content/uploads/2023/09/impact_of_size_and_volume_on_cryptocurrency_momentum_and_reversal.pdf) confirms Liu et al. (2022, *J. Finance*), Jia et al. (2022) and Dobrynskaya (2023) all find significant 1–4-week momentum, while Shen et al. (2020) and Kozlowski et al. (2020) find weekly **reversal** — the conflict resolves by universe: [Zaremba et al. (2021) review, via UGent thesis](https://libstore.ugent.be/fulltxt/RUG01/003/144/727/RUG01-003144727_2023_0001_AC.pdf) shows momentum studies use few large coins, reversal studies use many small coins; small-coin effects are bid-ask bounce/illiquidity artefacts.
- Long-term (6–12m) momentum is **not** significant: Grobys & Sapkota (2019, *Economics Letters*, 143 coins) — cited in the quantitative.cz paper above.
- Post-2021 honesty: [Cryptocurrency momentum has (not) its moments (Springer, Review of Quantitative Finance and Accounting, 2025)](https://link.springer.com/article/10.1007/s11408-025-00474-9) — performance is episodic; risk management (Barroso-style vol scaling) is needed to make it viable at all.

**Verdict:** the real, implementable edge is **weekly-rebalanced cross-sectional momentum in the top 10–30 liquid coins, long-only top bucket vs cash/stablecoin** — documented gross edge exists, but no peer-reviewed study gives a clean net Sharpe; honest estimate **0.4–0.8 net, regime-dependent**. Capacity at £100k–1M: trivial (top-20 alts trade $1B+/day).

**Access (this matters more than the edge):**
- IBKR UK crypto runs through **Paxos**: BTC, ETH, LTC, BCH (+ AAVE, PAXG, UNI added to Paxos July 2026). The wider 11–20-coin list (SOL, ADA, XRP, DOGE, AVAX, SUI, LINK…) is via **Zero Hash and primarily US** — UK eligibility for the Zero Hash lineup is not confirmed. Sources: [Good Money Guide UK review](https://goodmoneyguide.com/cryptocurrency/bitcoin/), [Investing in the Web](https://investingintheweb.com/brokers/interactive-brokers-crypto/), [LeapRate July 2026](https://www.leaprate.com/forex/brokers/interactive-brokers-expands-crypto-offering-with-new-tokens-and-stablecoin-transfers/), [Business Wire Mar 2025](https://www.businesswire.com/news/home/20250326324171/en/Interactive-Brokers-Expands-Crypto-Trading-with-New-Tokens).
- **Binance: effectively closed to new UK retail.** Onboarding suspended 16 Oct 2023 after the FCA blocked its promotions approver (Rebuildingsociety); still suspended. Spot crypto trading is not illegal for UK retail — but a new user can't get a Binance account. FCA-registered alternatives: Coinbase, Kraken. Sources: [The Block, Oct 2023](https://www.theblock.co/post/257721/binance-to-temporarily-stop-accepting-new-uk-users-after-fca-restriction), [InsideBitcoins](https://insidebitcoins.com/news/binance-uk-suspends-new-user-registrations-as-fca-clamps-down).
- **New door opened:** the FCA lifted the retail crypto-ETN ban on **8 Oct 2025** — BTC/ETH (and blended, e.g. 21Shares BTC+gold BOLD) ETNs on the LSE, ISA/SIPP-eligible. Sources: [FCA statement](https://www.fca.org.uk/news/statements/information-firms-offer-crypto-exchange-traded-notes), [City A.M.](https://www.cityam.com/fca-crypto-u-turn-retail-investors/), [The Block](https://www.theblock.co/post/385334/21shares-lists-bitcoin-gold-etp-on-lse).

---

## 2. ML cross-sectional equity ranking at daily bars

- Headline: [Gu, Kelly & Xiu (2020, RFS)](https://doi.org/10.1093/rfs/hhaa009) — decile long–short portfolios on NN forecasts earn gross Sharpe ≈ **2.45 equal-weighted / 1.35 value-weighted**, monthly rebalance, **full universe including microcaps**. Their NN **market-timing** version: Sharpe **0.77 vs 0.51** for buy-and-hold ([Mirova research summary](https://www.research-center.mirova.com/en/research-library/Empirical-Asset-Pricing-via-Machine-Learning)).
- The alpha lives in microcaps and the short leg. [Jo, Kim & Shin (2025)](https://iksa.or.kr/file/download/9320): a transaction-cost-aware conditional autoencoder earns **40% higher Sharpe than the plain model specifically when microcaps are excluded** — i.e., the plain GKX model degrades sharply ex-microcaps under costs.
- Post-cost replication table (Imperial College-hosted study): annualized Sharpe **0.12–1.28** across ML models depending on cost model ([Spiral, Imperial](https://spiral.imperial.ac.uk/server/api/core/bitstreams/c00deb79-130d-4c56-b4e7-8659e2828dc0/content)).

**Verdict:** I found **no credible 2020+ study showing a simple daily-bar ML ranking on 100–300 liquid large/mid caps holding Sharpe >1 net at retail costs**. Realistic net range for a liquid-universe GBM/NN ranking: **0.3–0.7**, highly correlated with vanilla momentum + short-term reversal. It is not the >1.2 engine; at best it marginally improves an existing momentum sleeve's signal quality.

---

## 3. Vol-managed factors — the highest-confidence uplift

- [Barroso & Santa-Clara (2015, JFE)](https://www.mse.ac.in/wp-content/uploads/2023/09/WORKING-PAPER-242.pdf): scaling momentum by its own realized vol raises Sharpe **0.53 → 0.97** (US), replicates internationally ([Lund thesis](https://lup.lub.lu.se/student-papers/record/8925261/file/8925267.pdf): "Sharpe is doubled in Europe"), eliminates crashes.
- [Daniel & Moskowitz (2016, JFE, "Momentum Crashes")](https://www.ivey.uwo.ca/media/3775547/momentumcrashes.pdf): the dynamic (state-conditional) momentum strategy delivers annualized **Sharpe 1.18**; crashes occur in panic states (market down + high vol + rebound) — a forecastable regime.
- [Moreira & Muir (2017, JF)](https://arno.uvt.nl/show.cgi?fid=188324): inverse-vol scaling raises Sharpe across market, value, momentum, profitability, ROE, investment, BAB **and FX carry**. Honest caveats: [Cederburg et al. (2020) show OOS fragility and Barroso & Detzel show plain vol management doesn't survive costs; a conditional multifactor version does, net](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13395).
- Retail-relevant: [Bongaerts, Kang & van Dijk (2020, FAJ, "Conditional Volatility Targeting")](https://repub.eur.nl/pub/130215/Bongaerts-Kang-van-Dijk-Conditional-volatility-targeting-2020-FAJ.pdf): the **unlevered** conditional strategy still doubled Sharpe (+0.18 avg across regions) with **turnover 0.4 vs 2.4** — cheap enough for daily-bar retail.

**Documented uplift on a trend core specifically: +0.1 to +0.3 Sharpe from plain vol targeting, +0.3 to +0.6 from state-conditional (panic-filter) versions.** This is the closest thing to a free lunch in the entire survey — it raises *s* on sleeves you already run rather than adding *N*.

---

## 4. Sector/industry rotation

- Academic anchor: Moskowitz & Grinblatt (1999, *J. Finance*) industry momentum (referenced throughout the literature above).
- Strong but **practitioner** evidence: [Concretum Group, "A Century of Profitable Industry Trends" (48 industries, 1926–2024)](https://concretumgroup.com/a-century-of-profitable-industry-trends/): industry trend-timing **Sharpe 1.46 gross vs 0.63** for the market, 18.5%/12.1% vol, ~60% lower max drawdown, **replicated with 31 SPDR sector ETFs over 20 years, profitable under high cost assumptions**. Flag: quant-firm white paper, not peer-reviewed; treat 1.46 as an upper bound.
- Weaker independent evidence: Antonacci's "Optimal Momentum" found sector momentum the *weakest* of geographic/style/sector ETF momentum (geographic Sharpe 0.64, 2003–2010, [CXO Advisory](https://www.cxoadvisory.com/momentum-investing/which-kind-of-etf-momentum-is-best/)); a 2020–2024 defense-ETF rotation backtest: 0.92 vs 0.71 S&P ([quantstrategy.io, practitioner](https://quantstrategy.io/blog/backtesting-momentum-strategies-for-defense-sector-etfs/)).

**Verdict:** realistic net **0.5–0.9**, UCITS-accessible (iShares/Xtrackers sector ETFs), costs trivial, capacity unlimited. But ρ with a broad index-trend sleeve is **0.6–0.8** — it's a *substitute/variant* of the trend sleeve, not an independent bet. Use it to enrich the trend sleeve's universe, not as separate N.

---

## 5. Tail-hedged / crisis-alpha overlays (spot-only)

- Buying tail insurance is a **negative-expected-return** proposition: [Ilmanen & Villalon, AQR, "Chasing Your Own Tail (Risk)" (2011) and Thapar's "Revisited"](https://swissquant.com/industry-insights/fat-tails-tail-risk/) — the documented reason CalPERS cited for dropping tail hedges ([context](https://www.nakedcapitalism.com/2020/05/04/universa-debunks-calpers-defense-of-1-billion-tail-hedge-miss-insider-accounts-expose-additional-misrepresentations.html)).
- Long-vol VIX-linked ETPs: structural contango decay; no UCITS long-vol product has a positive long-run Sharpe. Not a sleeve.
- **The spot-only crisis alpha is the defensive leg of trend/TAA itself**: [Hurst, Ooi & Pedersen (2017, JPM)](https://www.globalinvestments.net/investments/guides/trend-following-investing-guide) — trend positive in every decade since 1880 and positive in the worst equity drawdowns. Rotating to gilts/gold/cash on trend breaks *is* the tail hedge.

**Verdict:** zero independent spot-only tail overlays with positive expectancy. Crisis alpha comes bundled with trend — don't count it twice.

---

## 6. Intraday index momentum — inaccessible at daily bars

- [Baltussen, Da, Lammers & Martens (2021, JFE 142(1):377–403)](https://econpapers.repec.org/RePEc:eee:jfinec:v:142:y:2021:i:1:p:377-403): last-30-minute return is predicted by the rest-of-day return across 60+ futures, 1974–2020 (mechanism: gamma-hedging demand); reported gross timing Sharpe 0.87–1.73.
- **Why it's dead for this user:** the trade must be executed in the final 30 minutes of the US session (21:30–22:00 UK time) — the LSE is closed, UCITS ETFs can't trade it; US-domiciled ETFs are PRIIPs-blocked for UK retail; and the effect **reverts over the following days**, so daily bars capture none of it. Strictly a futures/intraday phenomenon. **Drop.**

---

## 7. The diversification math (computed)

S_combined = s√N / √(1+ρ(N−1)); ceiling as N→∞ = s/√ρ. Minimum N to reach 1.2:

| ρ | s=0.5 | s=0.6 | s=0.7 |
|---|---|---|---|
| 0.00 | 6 | 4 | 3 |
| 0.05 | 8 | 5 | 4 |
| 0.10 | 13 | 6 | 4 |
| 0.15 | 36 | 9 | 5 |
| 0.20 | **impossible** (ceiling 1.12) | 16 | 6 |
| 0.30 | impossible (0.91) | **impossible** (1.10) | 18 |
| 0.40 | impossible (0.79) | impossible (0.95) | **impossible** (1.11) |

Worked examples: s=0.6/N=6/ρ=0.1 → 1.20; s=0.65/N=5/ρ=0.1 → 1.23; s=0.7/N=6/ρ=0.2 → 1.21; s=0.5/N=15/ρ=0.1 → 1.25.

**The binding constraint is ρ, not N.** Genuinely low-correlation (ρ ≤ 0.2) spot-accessible sleeves plausibly number **4–7**: (i) equity index trend/TAA, (ii) short-vol/put-write, (iii) idiosyncratic event alpha (PEAD), (iv) FX carry, (v) crypto cross-sectional momentum, (vi) calendar overlays (though these ride equity beta), (vii) gold/rates trend (partially inside i).

---

## 8. Other documented candidates

- **Diversified carry** — [Koijen, Moskowitz, Pedersen & Vrugt (2018, JFE 127:197–225)](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2014/06/Carry.pdf): global carry factor **Sharpe 1.10 gross** (vs 0.47 passive), per-asset-class carry 0.5–0.9, **low cross-correlations** ([corroborating](https://riseofcarry.com/wp-content/uploads/2020/09/The_Carry_Trade_Michael_Rosenberg-1.pdf)). Spot-accessible portion: **G10 FX carry on IBKR spot** (long high-yielder vs GBP is inherently the trade; no derivatives needed): honest **0.4–0.6 net**, ρ ≈ 0–0.2 vs equity sleeves.
- **Trend century evidence** — [Hurst/Ooi/Pedersen (2017)](https://quantdecoded.com/en/trend-following-the-case-for-time-series-momentum): ~0.7 net over 136 years (futures, long/short); long-flat spot version ≈ 0.4–0.6. [Moskowitz/Ooi/Pedersen (2012)](https://www.pfolio.io/academy/time-series-momentum): composite TSMOM Sharpe ~1.0–1.28 gross — unreachable without futures/shorts.
- **QTAA/GTAA (Faber 2007)**: Sharpe 0.71 → 0.91 with broader asset-class diversification ([etf.com/Swedroe](https://www.etf.com/sections/index-investor-corner/swedroe-why-financial-trends-persist)) — practitioner, UCITS-implementable.
- **Dual momentum** — [GEM: 12.3% CAGR, max DD −33.7%, Sharpe 0.98, 1986–2026 backtest; CDM: 1.07](https://bestfolio.app/strategies/gem) ([CDM](https://bestfolio.app/strategies/cdm)) — practitioner backtests; expect post-publication decay to ~0.5–0.7 live ([an independent ETF replication got just 6.75%/yr](https://www.quantifiedstrategies.com/dual-momentum-trading-strategy/)).
- **Turn-of-month** — [McConnell & Xu (2008, FAJ)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9403956/); strategy Sharpe **0.77**, beating buy-and-hold with ~35% of time in market ([QuantPedia](https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes)). Fully implementable with UCITS index ETFs at daily bars; near-zero marginal cost. Rides equity beta (ρ high) but improves the equity sleeve's *s*.
- **Overnight anomaly** — [Lou, Polk & Skouras (2019, JFE)](https://personal.lse.ac.uk/polk/research/TugOfWar.pdf): anomaly profits earned overnight — but the LSE close (16:30) ≠ US close (21:00–22:00 UK), so the US overnight session is not cleanly capturable with LSE-listed ETFs, and US ETFs are PRIIPs-blocked. **Drop.**

---

## FINAL STACK — highest-Sharpe legal combination

| Sleeve | Contents | Honest net s | ρ vs book |
|---|---|---|---|
| A. Vol-managed trend super-sleeve | Long-flat TSMOM + dual momentum + sector rotation across UCITS equity regions/sectors, gilts, gold, BTC/ETH (Paxos or LSE cETNs); Barroso vol targeting + Daniel–Moskowitz panic filter; turn-of-month + FOMC calendar overlays folded in | **0.65–0.80** | — |
| B. Hedged put-write (from prior stack) | Index put-write, crash-hedged | 0.55–0.65 | ~0.4 vs A |
| C. Small-cap PEAD | Post-earnings drift, liquid names | 0.55–0.65 | ~0.25 |
| D. G10 FX carry basket | IBKR spot, vol-scaled | 0.40–0.55 | ~0.10 |
| E. Crypto cross-sectional momentum | Weekly top-3 of 10–20 liquid alts, long-only vs stablecoin (needs Coinbase/Kraken; Binance unavailable to new UK users; IBKR Paxos too narrow) | 0.40–0.70 | ~0.20 |

**Combined Sharpe math (equal risk budgets):** mean s = 0.60, average pairwise ρ ≈ 0.16, N = 5 → **S ≈ 1.05**. If vol management and calendar overlays genuinely lift each sleeve by the documented +0.1 (mean s = 0.70) → **S ≈ 1.22**. 

**Honest ceiling: ~1.0–1.3.** Beating 1.2 *net* requires the optimistic-but-documented branch: vol-managed sleeves each holding ≥ 0.65 net *and* average pairwise ρ staying ≤ 0.15. The prior round's 0.9–1.2 stands; this round adds three documented levers that push the top of the range: **(1) conditional vol targeting on every sleeve** (+0.1–0.3 each, Bongaerts et al. 2020; Daniel & Moskowitz 2016), **(2) FX carry as a genuinely uncorrelated fifth sleeve** (Koijen et al. 2018, GCF 1.10 gross), **(3) turn-of-month/FOMC calendar overlays on the equity sleeve** (0.77 standalone Sharpe, trivially implementable). The two seductive ideas that do *not* survive scrutiny for this user: intraday index momentum (needs futures/late-US-session access) and daily ML stock ranking (net Sharpe 0.3–0.7 ex-microcaps — an enhancer, not an engine).
