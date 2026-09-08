"""Frozen higher V27B paper profile, separate from every existing book."""
from dataclasses import dataclass
from hashlib import sha256
import json

ETFS = ("SPY", "EFA", "IYR", "GSG", "GLD", "TLT", "IEF", "UUP")
CIKS = {"AAPL":320193,"ABBV":1551152,"AMAT":6951,"AMD":2488,"AMZN":1018724,
        "AVGO":1730168,"BA":12927,"CAT":18230,"COST":909832,"GOOGL":1652044,
        "HD":354950,"JNJ":200406,"KO":21344,"MA":1141391,"MCD":63908,"META":1326801,
        "MRK":310158,"MSFT":789019,"MU":723125,"NFLX":1065280,"NKE":320187,
        "NVDA":1045810,"PEP":77476,"PFE":78003,"PG":80424,"QCOM":804328,
        "TSLA":1318605,"TXN":97476,"V":1403161,"WMT":104169,"XOM":34088}
SYMBOLS = (*ETFS, *sorted(CIKS))
CONTRACT = dict(version="v27b-forward-v1", profile="higher_5_12_joint", initial_gbp=100000,
    daily=.05, maximum=.12, original_maximum=.10, instrument=.01, aggregate=.03375,
    gross=2., name=.75, utilization=.9, internal_buffer=.25, fee_bps=5., stop_slippage_bps=5.,
    annual_holding_rate=0., slow_stop_atr=3., fast_stop_atr=2.5, fast_holding_sessions=5,
    slow_vol_target=.12, fast_max_names=4, paper_only=True, broker_enabled=False)
SPEC_HASH = sha256(json.dumps(CONTRACT,sort_keys=True,separators=(",",":")).encode()).hexdigest()

@dataclass(frozen=True)
class Spec:
    book_id: str = "v27b"
    label: str = "Book V27B · Joint trend / reversal"
    runtime_id: str = "__apex_book_v27b_forward_paper_runtime__"
    maximum_fx_age_days: int = 6

SPEC = Spec()
