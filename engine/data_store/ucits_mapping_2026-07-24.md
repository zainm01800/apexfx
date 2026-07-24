# UCITS MAPPING — UK-retail equivalents for the US instruments (2026-07-24)

**Purpose.** US-domiciled ETFs (SPY QQQ IWM XLK XLE XBI SMH SOXX) are blocked for UK retail on
IBKR (PRIIPs/KID — no US KIDs exist). This doc maps each one to a UK-retail-tradeable UCITS
line, states its halal status, and verifies Yahoo data availability through the engine's own
store/adapter path. **Reference document only — no gate, no ledger charge, no book change.**
Any universe change that follows from this mapping requires its own pre-registration.

**Verification method.** Live Yahoo chart-endpoint probe on 2026-07-24 via the engine's
`YahooAdapter` (exactly the path the Book H gate used for ISWD.L/ISDU.L/ISDE.L/SGLD.L/SPSK):
`engine/scratch/probe_ucits_candidates.py`, raw output `engine/scratch/probe_ucits_candidates.json`.
"In-window" = daily bars 2016-01-01 → 2024-12-31 (iteration window only; the 2025+ holdout was
not read). TERs are issuer-stated (justETF / BlackRock / SSGA pages, 2026); **the engine does not
model TER** (costs are per-class spread/slippage) — TERs are the compliance record only.
**KID:** every fund below is a genuine UCITS (Irish/Luxembourg domicile), so a PRIIPs KID exists
by construction and it is UK-retail tradeable on IBKR — that is the whole point of the mapping.

## Preference rules applied

1. **Islamic UCITS where they exist** (Sharia-certified): only for broad-market legs — ISWD.L /
   ISDU.L / ISDE.L, already validated in Book H (2273 in-window bars each, re-verified from cache).
2. **USD lines over GBp** — the ISWD.L artifact: a pence/sterling LSE line embeds GBP/USD in its
   daily returns (in-window corr vs SPY 0.385 for ISWD.L), and the engine has no quote-currency
   conversion. Where a USD LSE line exists it is the mapping; where only GBp exists it is flagged.
3. **Full in-window history preferred** (2273 bars = full 2016→2024 LSE calendar); late launches
   are flagged with their first bar.

## Mapping table

| US | UCITS equivalent (ticker, exchange, ccy) | TER | ISIN | Halal status | Yahoo verified (in-window) |
|---|---|---|---|---|---|
| **SPY** | **ISDU.L** — iShares MSCI USA Islamic, LSE, USD *(Sharia)* | ~0.30% | IE00B296QM64 | **HALAL-CERTIFIED** (MSCI USA Islamic; 33.33% screens, divergence documented in book_h_prereg §2) | ✓ 2273 bars, cache re-verified |
| | fallback: VUAA.L Vanguard S&P 500 (Acc), LSE, USD | 0.07% | IE00BFMXXD54 | conventional, unscreened → not halal | ✓ 1424 bars from 2019-05-14 (late) |
| **QQQ** | **CNDX.L** — iShares Nasdaq 100 (Acc), LSE, **USD** | 0.30% | IE00B53SZB19 | NOT certified. Nasdaq-100 excludes financials by index rule, but applies no activity/debt screens → fails the halal bar as a core leg; keep only with the same documented borderline call as XLK | ✓ 2273 bars, USD |
| | alt: EQQQ.L Invesco Nasdaq-100, LSE, **GBp** | 0.30% | IE0032077012 | as CNDX + pence artifact | ✓ 2273 bars but GBp — rejected (rule 2) |
| | **halal answer:** no Islamic Nasdaq-100 exists → ISDU.L already carries the US-mega-cap exposure | | | | |
| **IWM** | **XRSU.L** — Xtrackers Russell 2000 1C, LSE, **USD** | 0.30% | IE00BJZ2DD79 | NOT certified — and Russell 2000 holds ~15% financials/banks, so it fails the activity screen too, not just certification | ✓ 2273 bars, USD |
| | alt: WSML.L iShares MSCI World Small Cap, LSE, USD | 0.35% | IE00BF4RFH31 | NOT certified; world (not US) small cap; unscreened | ✓ 1708 bars from 2018-03-27 (late) |
| **XLK** | **IUIT.L** — iShares S&P 500 Info Tech Sector (Acc), LSE, **USD** | 0.15% | IE00B3WJKG14 | NOT certified; same documented borderline call as XLK itself (holds Visa/Mastercard, GICS-IT payment networks) — see book_h_prereg row 8 | ✓ 2273 bars, USD |
| | ~~IITU.L~~ — **same fund, same ISIN, GBp pence line** | 0.15% | IE00B3WJKG14 | as IUIT + pence artifact | ✓ 2273 bars but **GBp — the flagged candidate; rejected under rule 2** |
| **XLE** | **SXLE.L** — SPDR S&P US Energy Select Sector, LSE, **USD** | 0.15% | IE00BWBXM492 | NOT certified; energy sector passes the activity screen (no financials) — same rationale as Book H keeping XLE | ✓ 2273 bars, USD |
| **XBI** | **BTEC.L** — iShares Nasdaq US Biotechnology (Acc), LSE, USD | 0.35% | IE00BYXG2H39 | NOT certified; biotech passes the activity screen | ⚠ **partial only**: 1818 bars from 2017-10-19 (late), and a **different index** — Nasdaq biotech cap-weighted vs XBI's S&P Biotech Select equal-weight (small-cap tilt is not replicated) |
| **SMH / SOXX** | **SEMI.L** — iShares MSCI Global Semiconductors (Acc), LSE, **GBP** | 0.35% | IE000I8KRLL9 | NOT certified; semiconductors pass the activity screen; index is ESG-screened (not Sharia) | ⚠ **two strikes**: 843 bars from 2021-08-05 (very late) and **GBP sterling line** (no USD LSE line exists — SEMD.L probed, 404) |

## Explicit flags — instruments with NO clean equivalent

1. **IWM — no clean halal equivalent.** No Sharia-certified small-cap UCITS exists, and the only
   direct UCITS (XRSU.L) is doubly non-compliant (uncertified *and* ~15% financials). Options:
   (a) drop the small-cap leg — Book H already runs without one; (b) hold XRSU.L as a documented
   non-compliant exception — **not recommended**; (c) WSML.L world small-cap — still uncertified
   and only from 2018-03. Recommendation: **leave IWM unmapped** (dropped, exactly as Book H).
2. **SMH/SOXX — no clean data equivalent.** SEMI.L is the right exposure but pairs a GBP sterling
   line with a 2021-08 launch (843 in-window bars vs 2273). A 252-lookback trend signal gets its
   first valid observation only ~2022-08; CPCV windows before that simply skip it (the engine's
   late-listing path, same as PLTR/SOL). Useable for FORWARD trading, **not certifiable in-window**.
3. **XBI — exposure mismatch.** BTEC.L is the only biotech UCITS with real history, but it is
   cap-weighted Nasdaq biotech — XBI's equal-weight small-cap factor is what the Book H sleeve
   trades. Treat BTEC.L as a different instrument, not a replatforming. Forward-usable with that
   understanding; in-window usable from 2017-10 (1818 bars).
4. **QQQ — clean line exists (CNDX.L, USD, full history) but the exposure itself is not halal.**
   The halal answer for US mega-cap tech is the 12 screened single stocks + ISDU.L the book
   already runs, not a Nasdaq-100 tracker. Mapped for completeness; recommended use: none.
5. **IITU.L — the flagged candidate, confirmed dirty:** it is the **GBp pence line** of
   IE00B3WJKG14 (probe currency = GBp, price ~2736p). The clean USD line **IUIT.L** of the *same
   fund, same ISIN* was found and verified (USD, 2273 in-window bars). If XLK is ever
   replatformed, IUIT.L is the mapping, not IITU.L.

## Summary recommendations (for a future pre-registration, NOT this doc)

| US | Recommended UK-retail line | Status |
|---|---|---|
| SPY | ISDU.L (already in Book H) | clean, halal-certified, verified |
| QQQ | none (ISDU.L + the 12 stocks carry the exposure) | no halal equivalent |
| IWM | none (leg dropped, as in Book H) | **no clean equivalent** |
| XLK | IUIT.L (USD line, NOT IITU.L) | clean data; same borderline V/MA call as XLK |
| XLE | SXLE.L | clean data; activity-screen pass |
| XBI | BTEC.L (different index — partial) | late start 2017-10; exposure mismatch flagged |
| SMH/SOXX | SEMI.L (forward only) | GBP line + 2021 launch — not in-window certifiable |

## Sources

- justETF profiles: IITU/IUIT IE00B3WJKG14 (TER 0.15%, launch 2015-11-20), CNDX IE00B53SZB19
  (0.30%), XRSU IE00BJZ2DD79 (0.30%, launch 2015-03-06), SXLE IE00BWBXM492 (0.15%, launch
  2015-07-07), BTEC IE00BYXG2H39 (0.35%, launch 2017-10-19), SEMI IE000I8KRLL9 (0.35%, launch
  2021-08-03), WSML (0.35%).
- BlackRock fund pages (IUIT share-class currency USD, LSE listing; BTEC fact sheet TER 0.35%).
- SSGA UK pages (SXLE UCITS, XLE-select-sector index).
- Yahoo chart-endpoint probes: `engine/scratch/probe_ucits_candidates.json` (currencies, bar
  counts, max gaps ≤ 6 calendar days = holiday weekends, zero weekend bars everywhere).
