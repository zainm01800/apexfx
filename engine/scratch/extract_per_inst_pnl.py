import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd
from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean, PointInTimeAccessor
from apex_quant.backtest import PortfolioBacktester
from run_portfolio_gate import COMMON_PARAMS, TrendBook, WARMUP
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC
from run_portfolio_gate_multiasset import FX_MAJORS_7

cfg = get_config()
store = ParquetStore(cfg.store_path)
universe = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + list(FX_MAJORS_7)

master = {}
for inst in universe:
    df = store.load(inst, "1d")
    if not df.empty:
        master[inst] = clean(df)

pits = {k: PointInTimeAccessor(v) for k, v in master.items()}
timeframes = {k: "1d" for k in master}
book_params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
model = TrendBook(master, **book_params)

res = PortfolioBacktester(cfg, exit_mode="managed").run(
    pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252
)

total_months = 127.0  # 10.6 years = 127 months

trade_stats = {inst: {"pnl": 0.0, "wins": 0, "losses": 0, "trades": 0} for inst in master}

for t in res.trades:
    inst = t.instrument
    if inst in trade_stats:
        trade_stats[inst]["pnl"] += t.pnl
        trade_stats[inst]["trades"] += 1
        if t.pnl > 0:
            trade_stats[inst]["wins"] += 1
        elif t.pnl < 0:
            trade_stats[inst]["losses"] += 1

records = []
for inst, st in trade_stats.items():
    n_tr = st["trades"]
    if n_tr == 0:
        continue
    net_pnl = st["pnl"]
    win_rate = (st["wins"] / n_tr) * 100 if n_tr > 0 else 0.0
    trades_per_mo = n_tr / total_months
    asset_class = cfg.asset_class_of(inst)
    records.append({
        "symbol": inst,
        "asset_class": asset_class,
        "total_trades": n_tr,
        "trades_per_mo": trades_per_mo,
        "net_pnl": net_pnl,
        "win_rate": win_rate
    })

df_res = pd.DataFrame(records).sort_values(by="net_pnl", ascending=False)
print("SYMBOL       | CLASS      | TRADES | TR/MO | NET PNL       | WIN RATE")
print("-" * 70)
for idx, r in df_res.iterrows():
    print(f"{r['symbol']:12s} | {r['asset_class']:10s} | {r['total_trades']:6d} | {r['trades_per_mo']:5.2f} | ${r['net_pnl']:>11,.2f} | {r['win_rate']:7.1f}%")
