"""Run MANY backtests across instruments x strategies x configs and save every
result to the Supabase knowledge base (and the local JSON cache). This is the
"backtest a lot and learn from it" batch; the weekly GitHub Action re-runs it so
the knowledge base stays fresh and the Deep Analyse keeps getting smarter.

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_backtests.py                  # full universe
    .venv\\Scripts\\python.exe scripts/run_backtests.py EUR/USD AAPL BTC/USD   # specific
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.api.service import EngineService  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, clean, get_adapter  # noqa: E402
from apex_quant.storage import post_backtest, upsert_backtests  # noqa: E402
from apex_quant.validation.report import default_factory, ml_factory, run_validation  # noqa: E402

def _sweep():
    """(config_label, factory, param_grid). grid[0] is the headline config; the
    rest form the multiple-testing set DSR/PBO deflate against."""
    b = lambda mom, vol, rm="rule_based": {"momentum_lookback": mom, "vol_window": vol,
                                            "holding_horizon": 10, "reward_risk": 1.5, "regime_method": rm}
    return [
        ("baseline-mom21",  default_factory, [b(21, 21),  b(42, 21),  b(21, 21, "hmm")]),
        ("baseline-mom63",  default_factory, [b(63, 63),  b(126, 63), b(63, 63, "hmm")]),
        ("baseline-mom126", default_factory, [b(126, 126), b(63, 63), b(126, 126, "hmm")]),
        ("ml-gbm",          ml_factory, [{"model": "gbm", "holding_horizon": 10},
                                         {"model": "gbm", "holding_horizon": 15},
                                         {"model": "linear", "holding_horizon": 10}]),
    ]


def main(instruments: list[str]) -> None:
    cfg = get_config()
    instruments = instruments or cfg.universe
    adapter = get_adapter(cfg.data.provider)
    service = EngineService(cfg)
    end, start = "2024-12-31", "2014-01-01"
    rows, ok, fail = [], 0, 0

    for inst in instruments:
        klass = cfg.asset_class_of(inst)
        try:
            df = clean(adapter.get_history(inst, start, end))
            if len(df) < 300:
                print(f"skip {inst}: {len(df)} bars"); continue
            pit = PointInTimeAccessor(df)
        except Exception as e:  # noqa: BLE001
            print(f"skip {inst}: {type(e).__name__}: {e}"); continue
        print(f"[{klass}] {inst}: {len(df)} bars")

        for label, factory, grid in _sweep():
            try:
                rep = run_validation(pit, inst, strategy_factory=factory, param_grid=grid,
                                     cfg=cfg, generated_for=end)
                d = rep.model_dump()
                # local JSON cache (for the engine's /validation) keyed by the headline strategy
                service.save_validation(d, rep.strategy, inst)
                # online knowledge base row (per config)
                posted = post_backtest(d, config_label=label, timeframe=cfg.data.timeframe)
                ok += 1 if posted else 0
                fail += 0 if posted else 1
                rows.append((inst, label, rep.verdict["passed"], rep.dsr.get("dsr")))
                print(f"  {inst} {label}: {'PASS' if rep.verdict['passed'] else 'REJECT'} "
                      f"(DSR {rep.dsr.get('dsr', 0):.2f}, PBO {rep.pbo.get('pbo')}) -> supabase {'ok' if posted else 'FAILED'}")
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"  {inst} {label}: ERROR {type(e).__name__}: {e}")

    print(f"\nDONE: {ok} backtests saved to Supabase, {fail} failed. {len(rows)} total runs.")


if __name__ == "__main__":
    # Any positional args = explicit instrument ids (forex "EUR/USD", equity
    # "AAPL", crypto "BTC/USD"). No args = the full configured universe.
    main([a for a in sys.argv[1:] if a.strip()])
