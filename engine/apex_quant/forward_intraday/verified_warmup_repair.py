"""One independently retrieved SPY warm-up observation; never an inferred bar.

Twelve Data /time_series, symbol SPY, interval 1min, timezone UTC, start
2026-09-03 17:53:00, end 17:55:00, retrieved 8 September 2026. Response identity:
SPY / ETF / USD / MIC ARCX. Both neighbouring OHLCV rows agree with Yahoo
within its float precision. The missing 17:54 row was returned by the provider.
This is an explicit mixed-provider historical-input repair, not broker data.
"""
import numpy as np
import pandas as pd

COLUMNS=['open','high','low','close','volume']
STAMP=pd.Timestamp('2026-09-03T17:54:00Z')
RETRIEVED=pd.Timestamp('2026-09-08T16:06:00Z')
EVIDENCE={
    'provider':'Twelve Data time_series', 'symbol':'SPY', 'mic':'ARCX', 'currency':'USD',
    'interval':'1min', 'timestamp':STAMP.isoformat(), 'retrieved_at_utc':RETRIEVED.isoformat(),
    'response_sha256':'4e0c19e140f1092b98897b0638d224465b95c5579e5c7b53e15cd1143436616c',
    'method':'Exact returned bar; preceding and following Yahoo OHLCV independently matched',
    'historical_warmup_only':True,
}


def repair_retained_warmup(frame, now):
    """Missing-only, date-locked repair. Conflicting observations stay untouched."""
    if pd.Timestamp(now)<RETRIEVED or STAMP in frame.index:
        return frame,[]
    neighbours={
        STAMP-pd.Timedelta(minutes=1):[773.42999,773.45502,773.40002,773.44000,4790],
        STAMP+pd.Timedelta(minutes=1):[773.40308,773.46002,773.37000,773.45001,130027],
    }
    if any(t not in frame.index for t in neighbours):return frame,[]
    for t,values in neighbours.items():
        row=frame.loc[t,COLUMNS].to_numpy(float)
        if row.shape!=(5,) or not np.allclose(row[:4],values[:4],rtol=0,atol=.0001) or row[4]!=values[4]:
            return frame,[]
    result=frame.copy()
    result.loc[STAMP,COLUMNS]=[773.37,773.43,773.35,773.43,955]
    return result.sort_index(),[dict(EVIDENCE)]
