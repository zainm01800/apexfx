# PRE-REGISTRATION — Book H: the halal re-platformed multi-asset trend book (2026-07-19)

**Status: pre-registered BEFORE any run.** Ledger trials recorded before execution (see §6). This
document is the selection set and the methodology; changing anything after the run requires a new
pre-registration and new ledger charges.

**Base book:** Book D (`book_d_multiasset_252`, frozen forward-paper trend book): lookback 252,
vol 63, hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
config risk caps binding, per-asset-class v5 costs, daily bars, iteration window strictly
< 2025-01-01. Book H changes **the universe only** — signal, sizing, exits, costs, caps, window,
seed, and gate machinery are byte-identical. Any performance delta vs Book D is therefore
attributable to the halal constraint, which is exactly what this pre-registration isolates.

---

## 1. Audit rulings being implemented

1. Re-platform the equity/defensive legs to halal-certified instruments:
   - broad equity exposure → Islamic UCITS ETFs (flagship: iShares MSCI World Islamic UCITS ETF);
   - gold → an **allocated** physical gold ETC (AAOIFI SS57 reading: the security must represent
     an entitlement to specific allocated bars);
   - bonds → a sukuk ETF (the ruling named HSBC Global Sukuk UCITS ETF; see §2 row 9 for the
     data-driven substitution).
2. AAOIFI-style activity screen on every retained equity instrument: exclude conventional
   banks/insurers/financials, alcohol, gambling/casinos, pork-related, adult entertainment,
   weapons/defense; apply the <30% debt-to-market-cap filter where data allows, and document
   where it cannot be verified.

**Out of scope by user decision:** the 7 FX majors stay as-is (long+short kept). The tom-next
riba question at momentum horizons is flagged to the user's scholar — see §7 for the
quantification the user asked to have in writing. Crypto sleeve unchanged from Book D.

## 2. Substitution table (Book D equity sleeve → Book H)

Data verification column = live Yahoo chart-endpoint probe on 2026-07-19 (ticker, currency,
in-window daily bars < 2025-01-01, cached via the engine store path). TERs are issuer-stated
approximations — **the engine does not model TER** (its cost model is per-class spread/slippage:
equity 2.0 bps spread + 1.0 bps slippage per side); TERs are documented for the compliance
record only.

| # | Book D | Book H | Ruling / rationale | Data verified |
|---|--------|--------|--------------------|----------------|
| 1 | SPY, QQQ, IWM | **ISWD.L** — iShares MSCI World Islamic UCITS ETF (TER ~0.30%, full replication, tracks MSCI World Islamic) | Broad unscreened index exposure replaced by the halal-certified world-equity vehicle — the thesis of these legs was "world equity exposure". QQQ note: Nasdaq-100 excludes financials by construction but applies no activity/debt screens, so it goes too. | ISWD.L: GBp, 2273 bars 2016-01→2024-12 ✓ |
| 2 | — | **ISDU.L** — iShares MSCI USA Islamic UCITS ETF, USD line (TER ~0.30%) | Regional Islamic UCITS; data verified. (ISUS.L is the same fund's GBp line; the USD line is used for currency consistency.) Overlaps the 12 mega-caps the way QQQ did in Book D. | ISDU.L: USD, 2273 bars ✓ |
| 3 | — | **ISDE.L** — iShares MSCI EM Islamic UCITS ETF (TER ~0.35%) | Regional Islamic UCITS (emerging markets); adds the EM sleeve Book D never had; data verified. | ISDE.L: USD, 2273 bars ✓ |
| 4 | GLD | removed from core; **SGLD.L** — Invesco Physical Gold ETC (TER ~0.19%) — in the `+gold` config | GLD's unallocated-account mechanics are debated under AAOIFI SS57; the re-platformed gold leg is an explicitly **allocated** physical gold ETC. (Task hint said "PHAU.L Invesco" — attribution corrected: PHAU.L is WisdomTree Physical Gold; Invesco's USD line is SGLD.L. Both verified; SGLD.L chosen: USD line, allocated LBMA bars.) | SGLD.L: USD, 2273 bars ✓ (PHAU.L: USD, 2273 bars ✓) |
| 5 | TLT | removed from core; **SPSK** — SP Funds Dow Jones Global Sukuk ETF (TER ~0.5%, tracks Dow Jones Sukuk TR Index) — in the `+sukuk` config | 20y US Treasuries are pure riba; replaced by an investment-grade USD sukuk fund. **HSBC Global Sukuk UCITS ETF (the ruling's first choice) has no usable in-window data**: HSKD.L starts 2025-01-13 (entirely post-holdout-start), HBKS.L starts 2023-09 (~330 in-window bars, marginal), iShares $ Sukuk (SKUK) launched 2024-01 (≈170 in-window bars). SPSK is the larger-class alternative with a verified 5-year in-window history. | SPSK: USD, 1259 bars 2019-12→2024-12 ✓ |
| 6 | XLF | **dropped** | Conventional banks/insurers — fails the activity screen outright. | — |
| 7 | ARKK | **dropped** | High-level constituent check: holds Coinbase, Robinhood, Block (financial-activity names) plus early-stage names that fail debt-ratio screens. | — |
| 8 | XLK, XLE, XBI, SMH, SOXX | **kept** | High-level constituent check: SMH/SOXX pure semiconductors; XLE energy; XBI biotech — no banks/financials. **XLK borderline call, kept:** it holds Visa/Mastercard (~top-10 weights) — payment networks, fee-based revenue, GICS-classified IT, passed by mainstream halal screeners (they are not lenders); documented here so the user's scholar can overrule. | all already cached |
| 9 | 12 stocks: AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER | **kept** | All pass AAOIFI-style activity screens (no financials, alcohol, gambling, pork, adult, weapons). Cross-check: AAPL/MSFT/NVDA/META/AMZN/GOOGL/TSLA are long-standing top weights of MSCI World Islamic. **Methodology-dependent note:** NFLX and META are excluded by the stricter Dow Jones Islamic "entertainment" criterion in some vintages; they pass MSCI Islamic / AAOIFI-style activity screens — kept, flagged for the scholar. XLF was the only financial in Book D; no banks among the 12. | already cached |

**Screen methodology (financial ratios):** AAOIFI SS21 thresholds — interest-bearing debt /
market cap < 30%, interest-bearing deposits / market cap < 30%, non-compliant income < 5%
(with purification of the tainted dividend share). **Divergence documented:** the MSCI Islamic
indices underlying ISWD/ISDU/ISDE use the Malaysia-style **33.33%** thresholds (total debt,
cash + interest-bearing securities, and accounts-receivable + cash, each over trailing-12m
average market cap) — looser than AAOIFI's 30%. **Where verification is impossible, stated
plainly:** this engine has no point-in-time fundamentals feed, so debt-ratio compliance of the
12 stocks cannot be recomputed over the 2016–2024 backtest window; the activity screen is
structural (industry classification is stable), but the ratio screen is referenced to external
mainstream screeners at a point in time, not re-derived per bar. This is a certification-grade
limitation of the backtest, not of the forward portfolio.

## 3. Universe H (daily bars, iteration window strictly < 2025-01-01)

- **Core (38 instruments):** 12 stocks (row 9) + ISWD.L, ISDU.L, ISDE.L + XLK, XLE, XBI, SMH,
  SOXX (20 equity) + 11 crypto (BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOGE, LINK, ARB, SUI /USD —
  MATIC/USD has no cached 1d data and drops out exactly as in Book D) + 7 FX majors (EUR/USD,
  GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD).
- **+gold (39):** core + SGLD.L.
- **+sukuk (39):** core + SPSK (starts 2019-12-31; joins late like PLTR/UBER/SOL did — empty
  early CPCV-window slices are skipped by the fixed engine path).

Book D's universe was 42 (24 equity + 11 crypto + 7 FX). Book H core is 38 (20 equity + 11 + 7):
−7 dropped (SPY QQQ IWM GLD TLT XLF ARKK), +3 added (ISWD ISDU ISDE).

## 4. Pre-registered configurations (the full selection set: exactly 3 trials)

All three: Book D parameters verbatim — `momentum_lookback=252, vol_window=63,
holding_horizon=21, reward_risk=1.5, regime_method=rule_based, timeframe=1d, htf_rule=1w,
htf_ma_window=50, carry_filter=false`; managed exits; vol-scaled sizing; config risk caps binding
(`max_total_exposure 3.0`, `max_correlated_exposure 1.5`, `max_portfolio_risk 0.065`,
`max_risk_per_trade 0.02`, drawdown breakers 0.10/0.20); warmup 250; CPCV purge = 21.

| Config | Universe | Question it answers |
|---|---|---|
| `book_h_core_252` | core (38) | What does the equity re-platforming alone cost (SPY/QQQ/IWM/XLF/GLD/TLT/ARKK → ISWD/ISDU/ISDE)? |
| `book_h_gold_252` | core + SGLD.L (39) | What does re-adding the gold leg as an allocated ETC contribute? |
| `book_h_sukuk_252` | core + SPSK (39) | What does re-adding the defensive fixed-income leg as sukuk contribute? |

## 5. Gate criteria (identical machinery and thresholds to every prior gate)

DSR > 0.95 (deflated by the **full updated TrialLedger count**, recorded before the run) **and**
PBO < 0.5 (computed across all 3 configs — the whole pre-registered selection set; 16 splits,
4000 combos, seed 42) **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive.
Verdict per config: PASS only if all three gates pass. **Pre-registered honesty note:** the 3
configs share ~95% of their universe, so PBO's discriminative power is limited by construction
(same caveat as the 2-config C/D run) — it is reported as computed, pass or fail.

**Hypotheses:** (H1) Book H retains Book D's positive-OOS character (CPCV gate passes).
(H2) The halal-constraint cost, measured as Δ full-window Sharpe vs Book D clean (0.97), is
small relative to the compliance benefit. **Comparison anchor (Book D clean re-run,
2026-07-17):** Sharpe 0.97, PF 1.41, total return +438.5%, maxDD 19.1%, 1516 trades, win 55.9%,
expectancy +273.13/trade, DSR 0.934 @ n=150, PBO 0.056, CPCV med +0.050 (14/15 positive).

## 6. Ledger plan

`TrialLedger` loaded fresh: **n_trials = 190**. Exactly 3 new trials (`book_h_core_252`,
`book_h_gold_252`, `book_h_sukuk_252` with universe labels `book_h_core_38`, `book_h_gold_39`,
`book_h_sukuk_39`) recorded **before** the first run → **n = 193** deflates every DSR in the
Book H gate. No other configs will be evaluated in this campaign; any follow-up is a new
pre-registration.

## 7. FX riba note for the user's scholar (requested quantification)

The 7 FX majors stay in Book H per the user's decision. The tom-next swap/rollover interest
question at 21-day momentum horizons is a scholarly question, not an engineering one — but the
economic stakes are quantified from Book D's clean re-run: the FX sleeve produced **28 trades,
+3,598 net, = 0.79% of the book's net P&L** (+453,479 total). Removing FX from a Book D-style
book is therefore near-costless in expectancy terms; if the scholar rules against tom-next at
these horizons, the user can drop the sleeve knowing the measured contribution is < 1% of net.
Conversely the equity sleeve is 87% of net P&L — which is why the halal re-platforming focuses
there.

## 8. Known limitations (documented, not silently accepted)

- **ISWD.L is GBp-priced** (pence): its return stream embeds GBP/USD FX moves (in-window
  daily-return corr vs SPY is 0.385 — vs ~0.9 for a USD-priced world ETF). Momentum/vol signals
  are scale-invariant so mechanics are unaffected, but Book H's "world equity" leg carries cable
  noise Book D's SPY leg did not. The engine has no FX conversion of quote currencies — the same
  approximation Book D already runs on (USD equities + FX pairs on one equity curve).
- **TERs not modeled** — engine costs are spread/slippage only (§2). Islamic UCITS TERs
  (0.30–0.35%) are slightly above SPY (0.09%) and below ARKK (0.75%); net effect on a 21-day
  trend book is second-order but noted.
- **Debt-ratio screen is not point-in-time** (§2): activity screens are structural; ratio
  screens are referenced, not recomputed.
- **SPSK history is 5 years** (2020→), shorter than TLT's; it joins CPCV windows late, exactly
  like Book D's late listings (PLTR 2020, SOL 2021).
- **LSE vs NYSE calendars differ**; Yahoo bars reflect actual LSE sessions. Verified: zero
  weekend bars, max in-window gap 5 calendar days (holiday weekends), no holes.
- No `--final` holdout look will be run on Book H unless a config PASSES the gate, per the
  standing rule.
