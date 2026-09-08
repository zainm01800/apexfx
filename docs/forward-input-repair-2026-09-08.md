# Forward input recovery — 8 September 2026

These are paper-input repairs, not strategy changes or funded certification.
No ledger reset, broker order, synthetic bar or historical profit import.

## Sui identity

Yahoo's active Sui network instrument is
[SUI20947-USD](https://finance.yahoo.com/quote/SUI20947-USD/).
The previously requested SUI-USD has an inactive 0.0003-price history, ending
in June 2024. It is not the intended roughly dollar-priced Sui history.
Overlapping local Sui June–July 2026 closes and the correct Yahoo series have
median absolute relative difference 0.114%, maximum 0.527% (36 observations).

The mapping is corrected in Python and the website candle API. The paper
top-up uses a separate `YAHOO_SUI_USD_20947` cache from full correct-provider
history. The old unqualified cache remains untouched; a failed correct-source
fetch cannot fall back to the inactive token. All existing account histories
and parameters remain unchanged.

## V24/V30 retained-minute bootstrap

An actual Yahoo request returned 1,950 SPY minutes for 24–28 August, proving
that the old seven-day request window was narrower than available retention.
The collector now requests the past 28 days in chunks of at most seven days.
It still requires every exact official cash minute, positive valid OHLCV,
known corporate actions, publication-qualified FX and settlement delay.
It never overwrites existing frozen sessions.

One Yahoo minute, 3 September at 17:54 UTC, was null. Twelve Data's authenticated
time_series API returned that exact observation for SPY / USD / MIC ARCX:
open773.37, high773.43, low773.35, close773.43, volume955.
The preceding and following rows independently match Yahoo to 0.0001 in prices
and exactly in volume. The response SHA256 is recorded in
`verified_warmup_repair.py`; no credential is stored there.

This dated, missing-only observation is restricted to historical warm-up,
requires matching neighbours, and cannot apply before its retrieval time.
The runner explicitly rejects its use on an eligible execution session.
It is a disclosed mixed-provider input, not a claimed Yahoo observation or a
broker-certified quote. The live read-only check recovered 19 complete sessions
from 11 August through 4 September, with all required pre-8-September warm-up
sessions available. No 8 September trading session was simulated before close.

## Unresolved London ETF / SEC requirements

Yahoo daily data still omits 7 September for London ETFs. A UK session must not
be dropped just because the US was closed. The existing Twelve Data key's API
response says LSE data needs Grow/Venture access. No plan was purchased or
credential uploaded. Public daily histories were inspected but some ISDU/ISDE
highs and lows disagree with observed Yahoo hourly extremes; no approximate or
cross-provider daily candle was silently inserted into risk calculations.

V27B/V33 remain dependent on SEC access. A monitored contact email was requested
from the user; the filing-risk filter must remain enabled. See the official
[SEC developer access policy](https://www.sec.gov/about/developer-resources).

## Verification

Regression suites for intraday execution, accounting, state persistence,
daily completeness and the source-cache correction pass. The frontend suite
passes 65 tests. Production workflow outcomes must be checked separately after
publication; retained-history recovery alone is not proof of a successful trade.
