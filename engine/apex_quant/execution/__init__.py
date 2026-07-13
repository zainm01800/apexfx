"""Live execution bridges — MT4, ZeroMQ, and mock.

Every executor exposes the same ``submit_order()`` / ``push_order()`` interface
so the trading pipeline can swap providers via ``config.execution.provider``
without changing application code.

Provider selection
------------------
* ``"mt4"``  — File-based polling bridge (default, no extra deps).
* ``"zmq"``  — TCP ZeroMQ push bridge (<1 ms latency; requires pyzmq>=25).
* ``"mock"`` — In-process mock for testing.
"""

from apex_quant.execution.mt4_executor import MT4Executor
from apex_quant.execution.mock_executor import MockExecutor

try:
    from apex_quant.execution.zmq_bridge import ZMQBridge
    _ZMQ_AVAILABLE = True
except ImportError:
    _ZMQ_AVAILABLE = False

__all__ = ["MT4Executor", "MockExecutor"]
if _ZMQ_AVAILABLE:
    __all__.append("ZMQBridge")