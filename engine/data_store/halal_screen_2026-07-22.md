# Halal screen — 59 US large-cap additions to the SCAN universe (2026-07-22)

**Scope:** these names were added to `config.yaml` `data.equities`, which is the
**research/scan** universe (Deep Analyse, data cache, candidate pool for future gates).
**None of them is in any traded book.** The frozen forward book pins its own universe in
`scripts/run_paper_portfolio.py` (`BOOK_EQUITIES`) so this list can grow without touching the
experiment of record. Promoting any name into a book is a pre-registered, gated experiment
(`universe_expansion_prereg.md`).

## Method (identical to the Book H/I preregs, same honest limits)

1. **AAOIFI activity screen** — exclude conventional banking/insurance/riba income, alcohol,
   tobacco, gambling, pork and non-halal food, adult entertainment, weapons/defence.
2. **Financial-ratio screen (proxy)** — AAOIFI SS21 wants interest-bearing debt / market cap
   < 30%. **This engine has no point-in-time fundamentals feed**, so ratios cannot be
   recomputed historically. Present-day constituency in AAOIFI/Shariah-screened retail
   products (SP Funds SPUS, Wahed HLAL, iShares ISDU) is used as the proxy.
3. **Stated bias:** the constituency check is **present-day**, so selecting on it is
   survivorship/lookahead in universe construction — the same compromise carried (not fixed)
   since Book D. It is a reason to distrust any backtest run on this pool, and a further
   argument for the point-in-time data purchase.

## Included (59)

| Sector | Names |
|---|---|
| Tech / semis / software (18) | ADBE, CRM, ORCL, CSCO, INTC, AVGO, TXN, QCOM, MU, AMAT, LRCX, KLAC, ADI, NXPI, ANET, NOW, INTU, ACN |
| Healthcare (12) | JNJ, MRK, PFE, ABBV, LLY, TMO, ABT, DHR, SYK, ISRG, AMGN, VRTX |
| Consumer staples (7) | PG, KO, PEP, CL, KMB, MDLZ, MNST |
| Consumer discretionary (7) | NKE, HD, LOW, SBUX, TJX, ORLY, LULU |
| Industrials / materials (10) | LIN, UNP, ITW, HON, EMR, ETN, PH, APD, SHW, ECL |
| Energy (5) | XOM, CVX, COP, EOG, SLB |

All 59 verified to have ≥300 daily bars cached (23 already present, 36 fetched 2026-07-22
via the standard Yahoo adapter, `clean()`ed through the same pipeline as every other parquet).

## Excluded, with reasons (do not re-propose without new evidence)

| Excluded | Reason |
|---|---|
| JPM, BAC, GS, MS, WFC, C, AXP, BRK, BLK, SCHW, SPGI, CME, ICE, PGR, ALL, TRV, MET, PRU | Conventional banking / insurance / exchange — riba, fails activity screen outright |
| **V, MA** | Payment networks. Fee-based and passed by many mainstream screeners, but contested as individual holdings. Book H already documents them as a *borderline kept* inside XLK; held to the stricter line here. Flagged for the user's scholar |
| LMT, RTX, NOC, GD, BA, LHX, HII | Weapons / defence |
| MO, PM, BTI | Tobacco |
| STZ, TAP, BF.B, DEO | Alcohol |
| LVS, MGM, WYNN, DKNG, CZR | Gambling |
| MCD, TSN, HRL, SYY, YUM | Pork / non-halal food service |
| COST, WMT, KR, TGT | Grocery lines carrying alcohol, tobacco and pork; compliance contested across screeners — conservative exclusion |
| CAT, DE | Captive finance arms (interest income ≈5–7% of revenue) |
| AMT, PLD, EQIX, SPG, O | REITs — interest-based financing structures |
| NEE, DUK, SO, D | Utilities — activity clean but debt ratios routinely breach the 30% threshold |
| DAL, UAL, LUV, MAR, HLT | Airlines/hotels — high debt plus alcohol service |
| DIS | Entertainment + alcohol at parks; contested |
| NFLX, META | **Already in the book.** Noted for consistency: excluded by the stricter Dow Jones Islamic "entertainment" criterion in some vintages, passed by MSCI Islamic / AAOIFI activity screens. Kept, flagged — same ruling as Book H §2 |

## What this does and does not change

- **Does:** Deep Analyse can now research 83 equities (117 instruments total with crypto+FX);
  every future universe gate has a far deeper screened candidate pool.
- **Does NOT:** change the frozen forward book (still 24 equities + 12 crypto + 7 FX = 42
  instruments, MATIC excluded), change any certified result, or place a single order.
- **Reminder:** 12 of the scan universe's ETFs (SPY, QQQ, IWM, GLD, TLT, XLK, XLE, XLF, ARKK,
  SMH, SOXX, XBI) remain **untradeable** on a UK retail IBKR account under PRIIPs/KID. All 59
  additions here are plain shares and are therefore tradeable.
