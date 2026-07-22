# PRE-REGISTRATION — re-baselining the forward paper test onto Book H (2026-07-22)

**Status: pre-registered, NOT yet executed.** This is a decision record for switching the
forward test's book. No gate run and no new ledger charges are involved — Book H is already
certified (`book_h_prereg.md`, `book_h_gate.md`, ledger n=208). What changes is *which book
the forward test steps*, which is a material change to the experiment of record and therefore
gets written down BEFORE it happens, not after.

## 1. Why (evidence, not preference)

The running forward test is **Book D** (`book_d_multiasset_252`). On 2026-07-22 IBKR rejected
its IWM and QQQ entries outright:

> Error 201 — *No Trading Permission, Customer Ineligible... This product does not have a KID
> in English... Retail clients can trade packaged retail products only if an appropriate KID
> is available.*

This is the PRIIPs/KID rule, predicted in `ucits_mapping_2026-07-20.md` and now confirmed live.
It is structural, not transient:

- **Blocked (US-domiciled ETFs, PRIIPs products):** SPY QQQ IWM GLD TLT XLK XLE XLF ARKK SMH
  SOXX XBI — **12 of Book D's 24 equity names.**
- **Tradeable (plain shares/ADRs — NOT PRIIPs):** AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR
  TSM NFLX UBER. Confirmed by the natural experiment on 2026-07-22: all 6 share entries filled,
  all 3 ETF entries were refused.

**Consequence: the mirror can never replicate Book D on this account.** Half the equity sleeve
is unreachable, so measured real-vs-model divergence is permanently incomplete and the forward
record cannot answer "could this book actually have been traded?" — which is the entire point
of a forward test.

## 2. The change

Step the forward test with **`book_h_gold_252`** (certified 2026-07-19: DSR 0.996 @ n=205,
PBO 0.272, CPCV 14/15) instead of Book D. Book H drops the unscreened index/financial ETFs and
uses Islamic UCITS (ISWD.L→prefer ISDW, ISDU.L, ISDE.L) plus SGLD.L — all **KID-compliant and
tradeable by UK retail**. The halal constraint independently produced a legally-tradeable book;
this is that coincidence being cashed in.

## 3. Cost, stated honestly

- **The Book D forward record resets.** It began 2026-07-17 and holds ~5 days and 0 closed
  trades, so almost nothing of statistical value is lost — but it is a reset, and the clock on
  "how long have we forward tested" restarts from the re-baseline date. That cost rises every
  week this is deferred, which is the argument for doing it now rather than later.
- Book D's state.json and its existing records are **preserved, not deleted** (archived under
  `data_store/paper_portfolio/archive/book_d_<date>/`) so the record stays auditable.
- Book H's crypto sleeve remains unmirrorable on this account (FCA: UK retail cannot trade spot
  crypto at IBKR) and FX remains fine. So parity improves from "half the equity sleeve
  unreachable" to "equity + FX fully reachable, crypto engine-only" — an honest, documented
  and much smaller gap.

## 4. Pre-registered non-goals
- No parameter, universe, or risk change to Book H. It is stepped exactly as certified.
- No new trials; the DSR/PBO/CPCV evidence for Book H stands as gated. This document does not
  re-certify anything.
- The 2025+ holdout stays sealed.

## 5. Execution checklist (when the user approves)
1. Archive the Book D state + mirror records.
2. Point `run_paper_portfolio.py` at the Book H universe/params; seed fresh at the same
   starting equity.
3. Flatten or explicitly carry over the 6 live IBKR positions — **decide and record which**,
   because silently inheriting Book D positions into a Book H record would contaminate it.
4. First mirror run with `--attach-stops` so the new book is venue-protected from entry one.
5. Note the re-baseline date in `MEMORY.md`; the forward-test clock starts there.

## 6. Interim measure (already shipped, 2026-07-22)
Until the re-baseline happens, the mirror skips KID-blocked instruments up-front
(`KID_BLOCKED` in `run_ibkr_mirror.py`, env-overridable) instead of firing orders the venue
will always refuse. This stops the noise and records the divergence explicitly — it does NOT
fix the underlying mismatch, which only the re-baseline does.
