"""Live execution bridges — MT4, mock, and future brokers.

Every executor exposes the same ``submit_order()`` interface so the trading
pipeline can swap providers via ``config.execution.provider`` without changing
application code.
"""