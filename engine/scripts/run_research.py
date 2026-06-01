"""Precompute + cache AI research reports (LLM debate + per-hypothesis validation
is far too slow for a request). The API serves the cached JSON.

Uses the app's /api/ai LLM when ai.app_url is configured; otherwise falls back to
the programmatic proposer so the pipeline still works offline.

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_research.py EUR/USD
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.ai import AppAILLM, run_research  # noqa: E402
from apex_quant.ai.retrieval import PriorResult  # noqa: E402
from apex_quant.api.service import EngineService  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, clean, get_adapter  # noqa: E402


def _prior(service: EngineService, inst: str) -> list[PriorResult]:
    out = []
    for strat in ("regime_gated_momentum", "ml_gbm"):
        rep = service.validation(strat, inst)
        if rep:
            out.append(PriorResult(
                strategy=strat, passed=rep.get("verdict", {}).get("passed", False),
                dsr=rep.get("dsr", {}).get("dsr"), pbo=rep.get("pbo", {}).get("pbo"),
            ))
    return out


def main(instruments: list[str], n: int) -> None:
    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    service = EngineService(cfg)
    llm = AppAILLM(cfg.ai) if cfg.ai.app_url else None
    print("LLM:", f"app /api/ai @ {cfg.ai.app_url}" if (llm and llm.available) else "none (programmatic proposer)")

    for inst in instruments:
        print(f"\n=== AI research: {inst} (n={n}) ===")
        try:
            df = clean(adapter.get_history(inst, "2014-01-01", "2024-12-31"))
            pit = PointInTimeAccessor(df)
            report = run_research(pit, inst, llm=llm, cfg=cfg, n=n,
                                  prior_results=_prior(service, inst), generated_for="2024-12-31")
            path = service.save_research(report.model_dump(), inst)
            print(f"  {report.n_hypotheses} hypotheses (llm_used={report.llm_used}) -> {path}")
            for r in report.results:
                v = r.validation
                tag = (f"validated passed={v.get('passed')} dsr={v.get('dsr')} pbo={v.get('pbo')}"
                       if v else "validation skipped (discarded)")
                print(f"   - {r.label}: debate={r.debate['verdict']} | {tag}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {inst}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    n = next((int(a) for a in args if a.isdigit()), 3)
    instruments = [a for a in args if "/" in a] or ["EUR/USD"]
    main(instruments, n)
