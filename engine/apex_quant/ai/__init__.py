"""Phase 3 narrow-AI layer: retrieval-grounded hypothesis generation + debate.

Hard invariant: the LLM proposes HYPOTHESES that the validation engine judges.
No AI output is ever an order, and none sets a signal, size, or confidence.
"""

from apex_quant.ai.client import AppAILLM, DeepSeekLLM, GeminiLLM, FakeLLM, LLMClient, build_llm, extract_json
from apex_quant.ai.debate import DebateResult, run_debate
from apex_quant.ai.hypothesis import (
    Hypothesis,
    map_to_strategy,
    parse_llm_hypotheses,
    programmatic_proposer,
    sanitize_config,
)
from apex_quant.ai.pipeline import ResearchReport, run_research
from apex_quant.ai.retrieval import EvidencePack, PriorResult, gather_evidence

__all__ = [
    "LLMClient", "AppAILLM", "FakeLLM", "DeepSeekLLM", "GeminiLLM", "extract_json", "build_llm",
    "EvidencePack", "PriorResult", "gather_evidence",
    "Hypothesis", "sanitize_config", "map_to_strategy", "parse_llm_hypotheses",
    "programmatic_proposer", "DebateResult", "run_debate",
    "ResearchReport", "run_research",
]
