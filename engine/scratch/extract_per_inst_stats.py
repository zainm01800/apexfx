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

records = []
total_months = 127.0  # 10.6 years = 127 months

for inst, p_stats in res.per_instrument.items():
    n_tr = p_stats.get("n_trades", 0)
    net_pnl = p_stats.get("total_pnl", 0.0)
    win_rate = p_stats.get("win_rate", 0.0) * 100
    pf = p_stats.get("profit_factor", 0.0)
    trades_per_mo = n_tr / total_months
    asset_class = cfg.asset_class_of(inst)
    records.append({
        "symbol": inst,
        "asset_class": asset_class,
        "total_trades": n_tr,
        "trades_per_mo": trades_per_mo,
        "net_pnl": net_pnl,
        "win_rate": win_rate,
        "profit_factor": pf
    })

df_res = pd.DataFrame(records).sort_values(by="net_pnl", ascending=False)
print("SYMBOL       | CLASS      | TRADES | TR/MO | NET PNL       | WIN RATE | PF")
print("-" * 75)
for idx, r in df_res.iterrows():
    print(f"{r['symbol']:12s} | {r['asset_class']:10s} | {r['total_trades']:6d} | {r['trades_per_mo']:5.2f} | ${r['net_pnl']:>11,.2f} | {r['win_rate']:7.1f}% | {r['profit_factor']:.2f}")
