# Structural Profit Levers for a UK Trend Book: Sourced Report (Part 2)

**Baseline for all £-estimates:** £100k-equivalent book, Sharpe 0.5–1.0 → ~8–15%/yr gross, i.e. **£8–15k/yr profit**; worked examples use £10k gross. 5 round trips/week (260/yr). Same source-tier flags as before: **[primary/official]**, **[independent data]**, **[practitioner/affiliate — directional]**. Nothing here is tax advice — verify with an accountant before restructuring.

---

## 1. UK tax structure as a profit lever — *the largest single lever found*

**The rules (primary sources):**

- HMRC's Business Income Manual [BIM22015](https://www.gov.uk/hmrc-internal-manuals/business-income-manual/bim22015) **[primary]**: *"The taxpayer placing a spread bet is not normally carrying on a trade... **They are not taxable on the profits, nor do they receive relief for their losses.**"* — rooted in *Graham v Green* [1925]. Spread betting profits are exempt from CGT, income tax, and stamp duty. Exceptions live in BIM22020 (e.g., if the betting is part of an *existing taxable trade* — e.g., hedging a taxable business). The corollary worth noting: **losses get no relief either**.
- CFDs/stocks/crypto gains: CGT at **18% basic / 24% higher rate** (2025/26), annual exempt amount **£3,000** ([The Investors Centre tax table](https://www.theinvestorscentre.co.uk/trading/statistics/spread-betting/) **[practitioner, but consistent with HMRC rates]**).
- **Prop-firm payouts are NOT spread-bet-exempt.** They're treated as self-employment/trading income via Self Assessment ([FXify](https://fxify.com/blog/is-prop-trading-taxable/) **[firm blog]**, [OpesAdvisors](https://opesadvisors.com/ftmo/) **[practitioner]**, [TradersSecondBrain](https://traderssecondbrain.com/guides/prop-firm-taxes) **[practitioner]**). Income-tax bands [gov.uk](https://www.gov.uk/income-tax-rates) **[primary]**: £12,570 personal allowance; 20% to £50,270; 40% to £125,140. As self-employment income, Class 4 NICs (~6% within the main band) may also apply — confirm current rate with your accountant.

**After-tax on £10,000 profit (assuming personal allowance already used by other income):**

| Route | Tax | Keep | Drag |
|---|---|---|---|
| Spread bet (personal capital) | £0 | **£10,000** | 0% |
| CFDs (CGT, basic rate) | 18% × (£10k − £3k) = £1,260 | £8,740 | 12.6% |
| CFDs (CGT, higher rate) | 24% × £7k = £1,680 | £8,320 | 16.8% |
| Prop payout (income, basic) | 20% = £2,000 | £8,000 | 20% |
| Prop payout (income, higher) | 40% = £4,000 | £6,000 | 40% |

**£-impact: +£1,260–4,000/yr per £10k profit** depending on the alternative route and band. Spread betting beats CFDs at any band; it beats prop payouts *per pound of profit* (though prop is leveraged OPM — different capital equation, see §6 stacking).

**Instrument availability:** FX majors, gold, and indices are the *core* spread-bet markets (IG alone lists ~17,000 markets, [Good Money Guide](https://goodmoneyguide.com/trading/ig-spread-betting/) **[practitioner]**). **Crypto is the exception:** the FCA's retail ban on cryptoasset derivatives (CFDs, futures, options — and spread bets) still stands ([CryptoSlate UK guide](https://cryptoslate.com/crypto-exchanges/uk/) **[practitioner]**). Since **8 Oct 2025**, retail can buy FCA-listed **crypto ETNs** — and these are ISA/SIPP-eligible ([FCA statement](https://www.fca.org.uk/news/statements/information-firms-offer-crypto-exchange-traded-notes) **[primary]**, [The Block](https://www.theblock.co/post/385334/21shares-lists-bitcoin-gold-etp-on-lse)). So the crypto sleeve routes via ETNs in an ISA, spot (CGT), or a prop firm — not spread bets.

**Riba/financing cost — real and material for a trend book.** Spread bets are margined: positions held overnight pay daily financing of **benchmark + 2.5–3%** on the *full notional*: IG 2.5%±SONIA, CMC benchmark+2.5%, Spreadex 3%±SONIA ([Good Money Guide overnight-fee table](https://goodmoneyguide.com/trading/overnight-financing/) **[practitioner]**; CMC worked example: ~7%/yr on longs, [bellsforex audit](https://bellsforex.com/brokers/cmc-markets-market-review.html) **[practitioner]**). At SONIA ~4.25%, that's **~6.75–7.25%/yr on deployed notional**. On £100k at 50% average deployment: **~£3,400–3,600/yr** — *more than the spread costs*. Swap-free/"Islamic" accounts exist at some FCA brokers but are scarce in spread-bet form and typically widen spreads or add admin fees after a grace period — treat as a per-broker verification item, not a given. FX positions also pay/earn the tom-next differential, which can be a *credit* when you're long the higher-yielding currency — a trend book short EUR/USD currently earns carry. Net: for holds beyond ~2–3 days, financing dominates execution cost; it erodes (rarely reverses) the tax advantage only at high continuous deployment (at 1× full deployment, ~£7k/yr financing vs ~£1.3–2k tax saved on £10k profit — the tax still wins, but the margin narrows; at 2× deployment on a modest-profit year it can flip).

---

## 2. Copy-trading / parallel funded accounts — *near-linear income multiplication, explicitly allowed*

| Firm | Own-account copying | Cap / account limits | Source |
|---|---|---|---|
| **FTMO** | Internal copy **allowed**; external copy-IN banned | $400k base / $600k Prime / $1M Supreme | [THOR matrix](https://thortradecopier.com/blog/forex-prop-firms-that-allow-copy-trading-eas) **[practitioner]** |
| **FundedNext** | Own accounts **allowed** (cloud copiers banned) | $300k per EA/strategy | [THOR matrix](https://thortradecopier.com/blog/forex-prop-firms-that-allow-copy-trading-eas) |
| **The5ers** | Internal copy **permitted except Bootcamp** | Bootcamp: 4 accounts; Hyper Growth: $40k eval cap | [The5ers official](https://the5ers.com/challenge-programs-bootcamp-high-stakes-hyper-growth-explained/) **[primary]** |
| **FundingPips** | EAs/copy **allowed** "up to $300k allocation"; merge same-model Master accounts | $300k max allocation | [VettedPropFirms](https://vettedpropfirms.com/fundingpips-review/) / [thegodfunded](https://thegodfunded.com/en/firms/compare/fundingpips-vs-thetradingpit) **[practitioner]** |
| **E8 Markets** | Copy trading **openly supported**; merge allowed (not Signature) | Max **5 funded** accounts; unlimited evals | [E8 help center](https://helpfutures.e8markets.com/en/articles/10151739-can-i-have-multiple-accounts) **[primary]**, [QuantVPS](https://www.quantvps.com/blog/best-prop-firms-allow-copy-trading) **[practitioner]** |

**Payout math:** running identical signals on N accounts multiplies gross payouts ~N× at unchanged per-account risk. Consistency rules (FundedNext 40%, FundingPips 45/35/15% depending on model) are computed *per account*, so identical copying leaves the ratio unchanged on every account — no interaction penalty. What it does **not** do: diversify strategy risk — the equity curves are ~100% correlated, so a drawdown breaches all accounts together. What it **does** diversify: *firm-level* risk (payout denial, rule changes, firm failure — 80–100 firms exited in 2024 alone, [Finance Magnates via PropNavi](https://propnavi.io/en/blog/funded-vs-simulated-capital/) **[independent]**), and it staggers payout cycles and consistency clocks.

**£-impact:** 2×$100k accounts on a £10k/yr-per-book edge at 80% split ≈ **+£6–8k/yr net payouts vs one account**; 3–4 accounts linear until caps (FTMO $400k, FundingPips $300k, E8 5 accounts). Cost: one extra challenge fee per account (~€540/£460 each) plus admin. This is the cleanest "double income without touching per-trade risk" mechanism available.

---

## 3. Payout-structure optimization — *worth ~£1.5k/yr on split alone, plus one-off bonuses*

Current terms ([TradersSecondBrain FN vs The5ers](https://traderssecondbrain.com/guides/fundednext-vs-the5ers), [PropTradingVibes payout rules](https://proptradingvibes.com/blog/fundednext-payout-rules), [BestProps FTMO](https://bestprops.com/ftmo-review-for-funded-traders-2/) — all **[practitioner]**, [BlueGuardian payout review](https://www.blueguardian.com/blogs/best-prop-firms-for-quick-payouts-in-2026-fast-withdrawals-reviewed) **[firm blog]**):

- **Splits:** FTMO 80→90% (with scaling); FundedNext 80→90% via Pro, **95% via +30% fee add-on** (breakeven ≈ $900 of lifetime funded profit — buy it); FundingPips 60–100% (100% only at Hot Seat tier); The5ers 50→100% (slow ramp); E8 80%+.
- **Frequency/speed:** FTMO first payout at 14 days, then biweekly, ~8h processing; FundedNext **24h guarantee or $1,000 compensation** ([FundedTrading](https://fundedtrading.com/best-prop-firms-for-scaling/) **[practitioner]**); FundingPips weekly/biweekly/monthly/on-demand (choosing biweekly/monthly *avoids* the consistency rule entirely on Standard/1-Step — [PropFirmsFinder](https://propfirmsfinder.com/prop-firm/fundingpips/) **[practitioner]**); E8 request-anytime.
- **Fee refund:** FTMO refunds the ~€540 fee with first payout → effective cost of a *successful* evaluation ≈ £0.
- **FundedNext 15% challenge reward:** 15% of evaluation-phase profits paid with first funded withdrawal — on a $100k Stellar 2-Step pass (10%+5% = $15k simulated profit) that's **~$2,250 of real extra cash** no other major firm pays.
- The5ers sweetener: at $350k+ combined allocation, a **$4,000/month fixed salary** regardless of the month's P&L ([FundedTrading](https://fundedtrading.com/best-prop-firms-for-scaling/) **[practitioner]**) — unique, but deep into the survival curve.

**£-impact:** split 80%→95% on £10k gross = **+£1,500/yr**; FundedNext 15% challenge reward = **~£1,800 one-off** per $100k pass; FTMO fee refund = **~£460 one-off**. Payout *frequency* adds almost nothing to annual income, but faster withdrawal reduces your credit exposure to the firm — real economic value given sector attrition, just not line-item income.

---

## 4. Execution cost as income — *~£1k/yr on spreads; financing is the bigger, sleeper number*

**Spreads on EUR/USD (retail, UK):**

- **IBKR IDEALPRO:** ~0.1 pip interbank spread + 0.20 bps commission ($2 min per $100k per side → $4 RT) ≈ **0.4–0.6 pip all-in** ([MatchMyBroker](https://www.matchmybroker.com/articles/interactive-brokers-currency-conversion-guide), [InvestinGoal](https://ng.investingoal.com/forex/broker/stp/) — **[practitioner]**).
- **FCA spread bets:** Spreadex fixed 0.6 pip, no commission; Pepperstone ~0.6 typical (spread-bet.co.uk publishes monthly broker-reported spread data); IG ~0.6–1.0 variable ([spread-bet.co.uk platform review](https://www.spread-bet.co.uk/betting/platforms/), [The Investors Centre](https://www.theinvestorscentre.co.uk/trading/tradingview-spread-betting-brokers/) — **[practitioner]**).

**Verdict:** a cheap FCA spread bet (~0.6 pip, £0 commission) is *not* materially dearer than IBKR for majors — the raw difference is ~0.1–0.3 pip/RT. **£-impact of a 0.4-pip saving at 5 RT/week, 1× book notional ($127k ≈ $12.7/pip): 0.4 × $12.7 × 260 ≈ $1,320 ≈ £1,000/yr.** Scale linearly with your real average deployment.

**But the honest headline for a trend book:** with multi-day/week holds, **overnight financing exceeds total spread cost** (§1): ~£3.4k/yr at 50% deployment vs ~£2k/yr total spread at 0.6 pip. IBKR margin (~benchmark + 1.5% at retail tiers) undercuts spread-bet financing (+2.5–3%) by **~1–1.5%/yr of deployed notional ≈ £500–750/yr at 50% deployment** — a real offset against the spread-bet tax exemption for long-hold books. The break-even question is deployment × holding period vs annual profit; for your ~5 RT/week, multi-day book at Sharpe 0.5–1.0, **tax-free spread betting still nets ahead**, but by less than headline comparisons suggest. (On prop accounts, execution costs are baked into the simulation — you can't optimize them, only choose firms with tighter feeds/commissions, e.g., FundingPips $5/lot on evaluation models per [PropFirmTag](https://propfirmtag.com/prop-firm/fundingpips/) **[practitioner]**.)

---

## 5. Notional/scale structures — *real but slower than marketing implies at Sharpe 0.5–1.0*

- **FTMO scaling:** +25% of *original* balance per 4-month cycle, requiring **10% net profit in 4 months + 2 payouts** ([BestProps](https://bestprops.com/ftmo-review-for-funded-traders-2/) **[practitioner]**). 10%/4mo is a **~30%/yr pace** — a Sharpe 0.5–1.0 book (8–15%/yr) triggers every **~10–18 months, not 4**. Honest trajectory on $100k at ~12%/yr: yr 1 → $125k, yr 2 → ~$156k, yr 3 → ~$195k; income at 80–90% split: **~$10k → ~$14k → ~$20k**. The "$2M cap" is a ~20-year path at this edge.
- **The5ers:** doubling per 10% milestone sounds faster, but the **50% starting split** means yr-1 take-home is ~6% of notional ($6k on $100k) vs FTMO's ~$9.6k; crossover arrives ~yr 2–3 *if you survive* (and <1% of funded accounts see month 12 — FPFX). The $4k/month salary at $350k+ is a yr-3+ prospect, not a plan.
- **FundedNext Pro:** +25% balance + 90% split after 4 reward cycles with ≥4% growth each ([Tanto](https://tradetanto.com/learn/fundednext-rules-a-complete-guide-for-cfd-traders) **[practitioner]**; note [FundedTrading](https://fundedtrading.com/best-prop-firms-for-scaling/) claims +40%/cycle — treat exact increment as verify-before-purchase).
- **Multiple small vs one large account:** 2×$50k earns identically to 1×$100k — same notional, same signals. It does **not** compound faster. Its value is firm-risk hedging and staggered clocks (§2), not growth.
- **£-impact:** realistic scaling adds **+£2–4k/yr of incremental payout per surviving year** on a £10k-edge book. Meaningful, but the binding constraint is funded-phase survival, not the scaling schedule.

---

## 6. Everything else legitimate — ranked, with honesty flags

1. **The ISA wrapper for the ETF/crypto sleeves (£20k/yr allowance, [gov.uk](https://www.gov.uk/individual-savings-accounts) [primary]).** Rebalancing UCITS ETFs inside a Stocks & Shares ISA is CGT-free forever — same £1,260–1,680/yr saving per £10k of gains as spread betting, *with zero financing cost* (unleveraged) and no BIM22020 grey area. Since Oct 2025, **FCA-listed crypto ETNs are ISA-eligible** ([FCA](https://www.fca.org.uk/news/statements/information-firms-offer-crypto-exchange-traded-notes) [primary], [The Block](https://www.theblock.co/post/385334/21shares-lists-bitcoin-gold-etp-on-lse)) — the only tax-free route for the crypto sleeve. Constraint: no leverage, £20k/yr contribution cap, and frequent trading is fine but you can't short inside an ISA — a long-only trend sleeve fits; the short side must live elsewhere. **£-impact: up to ~£1.3–1.7k/yr per £10k gains, best risk-adjusted home for those legs.**
2. **Income stacking: personal spread-bet + prop on the same signals.** Marginal pounds of profit are taxed 0% (spread bet) vs 20–40% (prop payout), so per-pound-after-tax, personal SB wins — but prop requires no capital at risk. Optimal structure for a capital-constrained trader: challenge fees buy leveraged OPM; spare personal capital compounds tax-free in SB/ISA. Not an either/or — the signals are free to reuse. **£-impact: it's the sum of §1's saving applied to whatever personal capital you deploy.**
3. **Rebates/IB kickbacks [marginal].** Retail IB/rebate deals (~0.3–0.5 pip-equivalent cashback) exist at FCA brokers and can shave the §4 numbers further; prop accounts are simulated — no rebate economics. Worth £300–800/yr at most on a £100k retail book; legitimate but small.
4. **Carry-aware trend weighting [real but modest].** FX trend books already earn/pay tom-next; tilting position selection toward positive-carry signals when scores tie adds maybe 0.5–1%/yr on deployed FX notional. It's a tiebreaker, not a strategy.
5. **Calendar/"dead period" rules [mostly marketing].** No credible evidence of a tradeable calendar edge for a daily-bar trend book; news-window compliance (funded-stage 2-minute rules) is risk *avoidance*, not income. Skip.
6. **"Salary" and 100% split headlines [survivorship marketing].** The5ers' $4k/month salary and 100% splits, FundingPips' Hot Seat — real but reachable only past the point where <1% of funded traders remain (FPFX 12-month survival). Plan to yr-1 economics; treat these as upside.

---

## Summary table — estimated annual £-impact on a £100k book, Sharpe 0.5–1.0 (~£10k gross)

| Lever | £-impact/yr | Certainty |
|---|---|---|
| 1. Spread-bet/ISA tax routing vs CFDs (per £10k profit) | **+£1,260–1,680** (vs CGT); +£2,000–4,000 vs prop-taxed income | High (HMRC manual, primary) |
| 2. Second/third parallel funded account | **+£6–8k per extra $100k account** (gross payouts, same risk) | High (rules explicit) — minus ~£460/account fee |
| 3. Split 80→95% + challenge rewards + fee refund | **+£1,500/yr + ~£2,200 one-off** | High |
| 4. Spread optimization (0.4 pip on 5 RT/wk) | **+~£1,000** | Medium (deployment-dependent) |
| — Financing-cost awareness (SB +2.5–3% vs IBKR +1.5%) | **±£500–3,500 swing** depending on venue/hold length | Medium — the sleeper cost |
| 5. Scaling plans (realistic, at this Sharpe) | **+£2–4k incremental per surviving year** | Low (<1% survive 12 months) |
| 6. ISA for ETF/ETN sleeves | **+£1,300–1,700 per £10k gains** | High (gov.uk, FCA primary) |
| 7. Rebates, carry-tilt, calendar tricks | +£300–800 combined | Marginal/partly marketing |

**Priority order:** (1) route everything routable through spread bets + ISA first — it's the only lever that is both large and *certain*; (2) add a second funded account once the first passes — linear, cheap, rules-sanctioned; (3) pick payout terms deliberately (95% add-on, 15% challenge reward, fee refund); (4) audit financing drag before choosing venue for anything held >3 days; (5) treat scaling plans and 100%-split tiers as survivorship upside, not plan inputs.

**Key sources:** [HMRC BIM22015](https://www.gov.uk/hmrc-internal-manuals/business-income-manual/bim22015) · [gov.uk income tax bands](https://www.gov.uk/income-tax-rates) · [gov.uk ISAs](https://www.gov.uk/individual-savings-accounts) · [FCA on cETNs](https://www.fca.org.uk/news/statements/information-firms-offer-crypto-exchange-traded-notes) · [E8 multiple accounts (official)](https://helpfutures.e8markets.com/en/articles/10151739-can-i-have-multiple-accounts) · [The5ers programs (official)](https://the5ers.com/challenge-programs-bootcamp-high-stakes-hyper-growth-explained/) · [THOR copy-trading matrix](https://thortradecopier.com/blog/forex-prop-firms-that-allow-copy-trading-eas) · [PropTradingVibes payout/split rules](https://proptradingvibes.com/blog/fundednext-payout-rules) · [Good Money Guide overnight financing](https://goodmoneyguide.com/trading/overnight-financing/) · [MatchMyBroker on IBKR FX costs](https://www.matchmybroker.com/articles/interactive-brokers-currency-conversion-guide) · [spread-bet.co.uk platform spreads](https://www.spread-bet.co.uk/betting/platforms/).
