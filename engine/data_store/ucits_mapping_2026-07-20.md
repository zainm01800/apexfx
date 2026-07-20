# UCITS + halal mapping for the Book H universe — UK retail IBKR (2026-07-20)

**Purpose.** The certified book ("Book H + gold", `book_h_gold_252`) trades several
US-domiciled ETFs on the IBKR *paper* account. A UK retail IBKR account cannot buy
US-domiciled ETFs at all: PRIIPs requires a KID, US issuers do not produce them, so
IBKR blocks the order ticket. This doc maps every US-domiciled instrument in (or
adjacent to) the book to its tradeable UCITS equivalent, with halal status. It is a
**design doc only** — no universe change is adopted here; any actual swap must go
through a pre-registered gate (see T3).

**Verified facts carried over:** WisdomTree **PUTW is a dead product** (delisted —
do not re-propose put-write via ETF). **US-domiciled ETFs are blocked for UK
retail** (PRIIPs/KID). Both previously verified in this repo's research logs.

**Scope notes.**
- Plain **shares and ADRs are NOT PRIIPs products** — the 12 screened stocks
  (AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER) trade unchanged on
  a UK retail account. No mapping needed.
- **Crypto:** UK retail cannot trade spot crypto at IBKR (FCA restrictions). The
  crypto sleeve is paper-only until a separate venue decision is made. Out of
  scope here.
- **FX:** IDEALPRO spot FX is available to UK retail (margin FX). No mapping needed.

## The mapping

| Book instrument | Status in book | UCITS equivalent | LSE ticker | Ccy | TER | Index note | Halal status |
|---|---|---|---|---|---|---|---|
| SPY | **dropped** in Book H | — already replaced | ISDU | USD | 0.50% | MSCI USA Islamic | ✅ certified Islamic (AAOIFI-screened index) |
| QQQ | **dropped** in Book H | (Invesco EQQQ / iShares CNDX exist — EQQQ TER 0.30%) | EQQQ / CNDX | USD lines exist (EQQU for EQQQ) | 0.30% | Nasdaq-100 | ❌ NOT sharia-screened (no debt screens) — Book H's replacement is ISDU; do not re-add |
| IWM | **dropped** in Book H | (SPDR Russell 2000 UCITS exists) | R2SC | USD | 0.30% | Russell 2000 | ❌ NOT sharia-screened (includes banks/insurers) — stays dropped |
| XLK | kept (paper) | iShares S&P 500 Information Technology Sector UCITS | **IITU** | USD | **0.15%** | S&P 500 Capped 35/20 IT — same sector methodology as XLK | ⚠️ conditionally acceptable: same V/MA-payments borderline call already documented in the Book H prereg; activity fine, no debt-ratio screen |
| XLE | kept (paper) — **T3 tests removing it** | iShares S&P 500 Energy Sector UCITS | **IUES** | USD | 0.15% | S&P 500 Capped 35/20 Energy | ⚠️ activity halal (no riba business), no debt screen; XOM+CVX ≈ 48% concentration. May be moot if T3 prunes XLE |
| XBI | kept (paper) | iShares Nasdaq US Biotechnology UCITS | **BTEC** (USD) / BTEK (GBP) | USD | 0.35% | ⚠️ **different index**: Nasdaq Biotech (cap-weighted) vs XBI's S&P equal-weight — higher mega-cap concentration, different return profile. A gate must re-test, not assume equivalence | ⚠️ activity fine (biotech), no debt screen |
| SMH | kept (paper) | VanEck Semiconductor UCITS | **SMH.L** (USD) / SMGB (GBP) | USD | 0.35% | MarketVector US-Listed Semiconductor 10% Capped — near-twin of US SMH | ⚠️ activity fine, no debt screen |
| SOXX | kept (paper) | **no US-only PHLX equivalent exists.** Closest: iShares MSCI Global Semiconductors UCITS | **SEMI** | USD | 0.35% | MSCI ACWI IMI Semis (global, ESG-screened) — NOT US-only; overlaps heavily with SMH-UCITS | ⚠️ flag: on a UCITS-only account, holding both SMH-UCITS and SEMI is near-duplicate exposure. Recommend keeping only one (decision belongs to a T3-style gate, not this doc) |
| SGLD.L | in book (gold config) | already UCITS-world (Irish ETC, KID available) | **SGLD** (USD) / SGLP (GBP) / SGLS (GBP-hedged) | USD | 0.12% | LBMA PM gold, allocated bars (JPM London vaults) | ✅ AAOIFI SS57-compliant allocated gold — already validated |
| SPSK | sukuk config only (not certified book) | **SPSK is US-domiciled → blocked for UK retail.** UCITS alternative: HSBC Global Sukuk UCITS | **HSKD** | USD | 0.37% | FTSE IdealRatings Sukuk (USD IG sukuk) | ✅ sharia-native product. ⚠️ known limitation (already in gate script comments): HSKD has no in-window (≤2024) price history — cannot be backtested through the standard gate; would need a forward-only incubation |
| ISWD.L | in book | (already UCITS) — **currency-line fix**: LSE **ISWD is the GBp pence line** (embeds GBP/USD); the **USD line is ISDW** | prefer **ISDW** | USD | 0.30% | MSCI World Islamic | ✅ certified Islamic |
| ISDU.L | in book | already UCITS, already USD line | ISDU | USD | 0.50% | MSCI USA Islamic | ✅ certified Islamic |
| ISDE.L | in book | already UCITS | ISDE | USD | 0.85% | MSCI EM Islamic | ✅ certified Islamic |

## Explicit gaps / flags (nothing hidden)

1. **SOXX has no clean UCITS equivalent.** SEMI is global-universe and largely
   redundant with SMH-UCITS. A real-money UCITS book should carry ONE semis ETF.
2. **XBI → BTEC is an index change**, not a wrapper change (equal-weight → cap-
   weight). Backtest evidence for XBI does NOT transfer; BTEC must be gated on its
   own history before any real-money use.
3. **HSKD (sukuk) cannot pass the standard gate** — no in-window history. If sukuk
   exposure is ever wanted, it enters via forward-paper incubation only.
4. **No debt-ratio screening on sector ETFs** (IITU/IUES/BTEC/SMH/SEMI): activity
   screens pass, but AAOIFI financial-ratio screens are not applied by these
   funds. The Islamic-certified alternatives (ISDU/ISDW/ISDE) remain the only
   fully-screened equity wrappers. This is the same compromise already accepted
   and documented for XLK/XLE/XBI/SMH/SOXX in the Book H prereg — the UCITS swap
   neither improves nor worsens it.
5. **Currency lines:** prefer USD lines everywhere (ISDW not ISWD, BTEC not BTEK,
   SMH.L not SMGB, SGLD not SGLP) so the book's USD-denominated signals aren't
   contaminated by an embedded GBP/USD leg. GBP lines are the same fund, but the
   *price series* used for signals must then be FX-adjusted — simpler to avoid.
6. **TER drag is real but small at this book's turnover:** worst case (BTEC/SMH/
   SEMI at 0.35%, ISDE at 0.85%) vs the US originals (XBI 0.35%, SMH 0.35%,
   SOXX 0.35%) — sector-fund TERs are nearly identical; only the Islamic
   wrappers cost more (0.30–0.85% vs 0.07–0.20% for vanilla), which is the price
   of the screen, already embedded in the certified Book H backtest via ISWD/
   ISDU/ISDE price history.

## Sources (verified 2026-07-20)

- IITU: [justETF IE00B3WJKG14](https://www.justetf.com/uk/etf-profile.html?isin=IE00B3WJKG14), [Yahoo IITU.L](https://uk.finance.yahoo.com/quote/IITU.L/)
- IUES: [iShares product page](https://www.ishares.com/uk/individual/en/products/280503/ishares-sp-500-energy-sector-ucits-etf), [justETF IE00B42NKQ00](https://www.justetf.com/en/etf-profile.html?isin=IE00B42NKQ00)
- SMH UCITS: [justETF IE00BMC38736](https://www.justetf.com/en/etf-profile.html?isin=IE00BMC38736), [VanEck UK](https://www.vaneck.com/uk/en/investments/semiconductor-etf/), [Yahoo SMH.L](https://uk.finance.yahoo.com/quote/SMH.L/)
- BTEC/BTEK: [justETF IE00BYXG2H39](https://www.justetf.com/en/etf-profile.html?isin=IE00BYXG2H39), [iShares BTEC](https://www.ishares.com/uk/individual/en/products/291450/ishares-nasdaq-us-biotechnology-ucits-etf-fund)
- SEMI: [iShares SEMI](https://www.ishares.com/uk/professional/en/products/319084/ishares-msci-global-semiconductors-ucits-etf), [Yahoo SEMI.L](https://finance.yahoo.com/quote/SEMI.L/)
- R2SC: [justETF IE00BJ38QD84](https://www.justetf.com/uk/etf-profile.html?isin=IE00BJ38QD84)
- EQQQ: [justETF IE0032077012](https://www.justetf.com/en/etf-profile.html?isin=IE0032077012), [Invesco UK](https://www.invesco.com/uk/en/financial-products/etfs/invesco-eqqq-nasdaq-100-ucits-etf-dist.html)
- HSKD sukuk: [justETF IE000E8WZD37](https://www.justetf.com/en/etf-profile.html?isin=IE000E8WZD37), [Bloomberg HSKD:LN](https://www.bloomberg.com/quote/HSKD:LN)
- ISWD/ISDW lines: [justETF IE00B27YCN58](https://www.justetf.com/en/etf-profile.html?isin=IE00B27YCN58), [Yahoo ISDW.L](https://finance.yahoo.com/quote/ISDW.L/), [iShares ISWD](https://www.ishares.com/uk/individual/en/products/251394/ishares-msci-world-islamic-ucits-etf)
- ISDE: [iShares ISDE](https://www.ishares.com/uk/individual/en/products/251392/ishares-msci-emerging-markets-islamic-ucits-etf), [Yahoo ISDE.L](https://finance.yahoo.com/quote/ISDE.L/)
- SGLD/SGLP/SGLS: [Invesco UK](https://www.invesco.com/uk/en/financial-products/etfs/invesco-physical-gold-etc.html), [justETF IE00B579F325](https://www.justetf.com/en/etf-profile.html?isin=IE00B579F325) (TER 0.12% confirmed)
- ISDU/ISDE TERs: [justETF IE00B296QM64](https://www.justetf.com/en/etf-profile.html?isin=IE00B296QM64), [halaletfs.co.uk](https://halaletfs.co.uk/) (Islamic UCITS TER range 0.49–0.85% corroborated)
