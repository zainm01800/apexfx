"""Every blocking ib_async request must be bounded (REQUEST_TIMEOUT_S).

Observed 2026-07-24: the live scan hung mid-cycle with 0% CPU. A faulthandler
dump showed all 15 ThreadPoolExecutor workers inside::

    scan_single_asset -> fetch_live_account_state -> IBKRExecutor.get_account
    -> ib.accountSummary -> IB._run -> asyncio run_until_complete   (forever)

ib_async's ``IB.RequestTimeout`` defaults to ``0`` — no timeout — so a gateway
that accepts the API socket but never answers a request blocks the caller
indefinitely, and the executor's ``as_completed`` wait never returns. The fix
sets ``RequestTimeout`` once on the IB instance at connect time, so a
brain-dead gateway raises ``asyncio.TimeoutError`` and callers fall back
(``fetch_live_account_state``) instead of hanging the engine.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from apex_quant.execution import ibkr_executor as ex

ENGINE_DIR = Path(__file__).resolve().parent.parent


def test_request_timeout_constant_is_positive_and_sane():
    assert ex.REQUEST_TIMEOUT_S > 0, "0 is ib_async's infinite default"
    assert ex.REQUEST_TIMEOUT_S <= 120, "a request bound must not become a hang itself"


def test_connect_applies_the_timeout_to_the_ib_instance():
    src = inspect.getsource(ex.IBKRExecutor.connect)
    assert "self._ib.RequestTimeout = REQUEST_TIMEOUT_S" in src, (
        "the timeout must be set on the IB instance — IB._run reads it per call"
    )
    # it must be set where the instance is created, before any request is made
    assert src.index("RequestTimeout") < src.index("managedAccounts"), (
        "set it before the first blocking call (managedAccounts / accountSummary)"
    )


def test_ib_async_run_really_honours_request_timeout():
    """The contract the fix relies on: IB._run passes self.RequestTimeout to util.run."""
    ib_py = (ENGINE_DIR / ".venv-mac" / "lib" / "python3.12" / "site-packages"
             / "ib_async" / "ib.py")
    src = ib_py.read_text(encoding="utf-8")
    assert "util.run(*awaitables, timeout=self.RequestTimeout)" in src
    assert "RequestTimeout: float = 0" in src, (
        "if ib_async ever ships a non-zero default, this fix may be redundant — re-check"
    )
