"""Phase 3 narrow-AI layer: retrieval-grounded hypothesis generation + debate.

Hard invariant: the LLM proposes HYPOTHESES that the validation engine judges.
No AI output is ever an order, and none sets a signal, size, or confidence.
"""

from apex_quant.ai.client import AppAILLM, FakeLLM, LLMClient, extract_json
from apex_quant.ai.retrieval import EvidencePack, PriorResult, gather_evidence

__all__ = [
    "LLMClient", "AppAILLM", "FakeLLM", "extract_json",
    "EvidencePack", "PriorResult", "gather_evidence",
]
