"""COT data layer: parsing, engine-signing, point-in-time release shift, z-score."""

import pandas as pd
import pytest

from apex_quant.data.cot import (
    as_of_release,
    net_positioning,
    parse_cot,
    positioning_zscore,
)

FIXTURE = """"Market and Exchange Names","As of Date in Form YYYY-MM-DD","Open Interest (All)","Noncommercial Positions-Long (All)","Noncommercial Positions-Short (All)","Extra Col"
"EURO FX - CHICAGO MERCANTILE EXCHANGE","2024-01-02",100000,60000,20000,1
"EURO FX - CHICAGO MERCANTILE EXCHANGE","2024-01-09",100000,50000,30000,1
"JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE","2024-01-02",50000,10000,30000,1
"GOLD - COMMODITY EXCHANGE INC.","2024-01-02",200000,150000,50000,1
"""


def test_parse_keeps_expected_columns_and_types():
    df = parse_cot(FIXTURE)
    assert set(df.columns) == {"market", "date", "noncomm_long", "noncomm_short", "open_interest"}
    assert len(df) == 4 and df["date"].dtype.kind == "M"


def test_net_positioning_share_of_oi_and_usd_inversion():
    df = parse_cot(FIXTURE)
    eur = net_positioning(df, "EUR/USD")
    assert eur.iloc[0] == pytest.approx(0.40)          # (60k-20k)/100k specs long EUR
    assert eur.iloc[1] == pytest.approx(0.20)
    jpy = net_positioning(df, "USD/JPY")               # specs SHORT yen -> LONG USD/JPY
    assert jpy.iloc[0] == pytest.approx(+0.40)         # -(10k-30k)/50k, sign flipped
    gold = net_positioning(df, "GOLD")
    assert gold.iloc[0] == pytest.approx(0.50)
    with pytest.raises(KeyError):
        net_positioning(df, "BTC/USD")                 # unmapped instrument is loud, not silent


def test_release_shift_is_three_business_days():
    df = parse_cot(FIXTURE)
    eur = net_positioning(df, "EUR/USD")               # Tuesdays 2024-01-02 / 01-09
    rel = as_of_release(eur)
    assert rel.index[0] == pd.Timestamp("2024-01-05")  # Tue obs -> Fri release
    assert rel.index[1] == pd.Timestamp("2024-01-12")
    assert list(rel.values) == list(eur.values)        # values untouched, only availability


def test_zscore_flags_extremes_with_min_history():
    idx = pd.date_range("2020-01-07", periods=60, freq="W-TUE")
    net = pd.Series([0.10] * 59 + [0.60], index=idx)   # long flat history then a spike
    z = positioning_zscore(net, window=156, min_periods=52)
    assert z.iloc[:51].isna().all()                    # nothing reported before min history
    assert z.iloc[-1] > 3                              # the spike is a hard extreme
    flat = positioning_zscore(pd.Series([0.2] * 60, index=idx), min_periods=52)
    assert flat.iloc[55:].isna().all()                 # zero-sigma history -> NaN, never inf
