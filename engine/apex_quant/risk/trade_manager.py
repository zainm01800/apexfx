"""Trade Management System (TMS) Manager.

Encapsulates all 5 trade management techniques (partial closes, breakeven moves,
chandelier trailing, time-based exits, and volatility squeeze tightening)
into a reusable class that can be used by both the live trading script
and the backtester for exact simulation parity.
"""

from __future__ import annotations

import logging
from typing import Tuple, List, Dict, Callable

logger = logging.getLogger("apex_quant.risk.trade_manager")


class TradeManager:
    def __init__(
        self,
        p1_r: float = 1.0,
        p1_pct: float = 0.50,
        p2_r: float = 1.5,
        p2_pct: float = 0.25,
        be_buffer_pips: float = 3.0,
        chandelier_mult: float = 2.0,
        squeeze_mult: float = 1.0,
        time_stop_bars: dict[str, int] | None = None,
        runner_mode: bool = False,
    ) -> None:
        self.p1_r = p1_r
        self.p1_pct = p1_pct
        self.p2_r = p2_r
        self.p2_pct = p2_pct
        # RUNNER MODE (pre-registered experiment, default OFF — the certified book
        # keeps its fixed target). When True: after Partial 1 the remaining half is
        # NOT capped at the fixed target and NOT trimmed by Partial 2 — it rides the
        # Chandelier trail uncapped, so a 252-day momentum entry can actually catch a
        # multi-R trend leg instead of being amputated at 1.5R. See
        # engine/data_store/runner_exit_prereg.md.
        self.runner_mode = runner_mode
        # Breakeven-stop buffer in PIPS (multiplied by pip_size at apply time).
        # 3.0 pips matches the legacy live path's intent (0.0003 price on a
        # 5-decimal pair); the old 0.0003 default collapsed to ~3e-8 price once
        # multiplied by pip_size, so managed BE exits booked -cost every time.
        self.be_buffer_pips = be_buffer_pips
        self.chandelier_mult = chandelier_mult
        self.squeeze_mult = squeeze_mult
        self.time_stop_bars = time_stop_bars or {"15m": 8, "1h": 10, "1d": 7, "1w": 3}

    def init_position_tms(self, position: dict) -> dict:
        """Initialize TMS metadata flags on a new position dictionary."""
        position.setdefault("tms_p1", False)
        position.setdefault("tms_p2", False)
        position.setdefault("tms_be", False)
        position.setdefault("bars_open", 0)
        position.setdefault("tms_log", [])
        position.setdefault("initial_units", position["units"])
        return position

    def update_position(
        self,
        position: dict,
        high: float,
        low: float,
        close: float,
        atr: float,
        is_squeeze: bool,
        bars_history: list[dict],  # list of dicts with {"high", "low", "close"}
        timeframe: str,
        pip_size: float,
        fill_fn: Callable[[float, bool], float],
        max_bars: int | None = None,
        open_: float | None = None,
    ) -> Tuple[float, str]:
        """Update position state (stops, partial closes) based on the current bar.

        Args:
            position:     Dict representing the position. Will be mutated in-place.
            high:         Bar high price.
            low:          Bar low price.
            open_:        Bar OPEN price. When given, a stop that gapped through is
                          filled at the open (the worse level) instead of at the stop
                          — see the gap-aware note in the stop-out block. Optional so
                          existing callers keep their previous behaviour.
            close:        Bar close price.
            atr:          Current ATR value.
            is_squeeze:   True if volatility squeeze is active.
            bars_history: Past bars window (min 22 bars needed for Chandelier trail).
            timeframe:    Timeframe string (e.g. '1h', '15m').
            pip_size:     Pip size for the instrument.
            fill_fn:      Callback `fill_fn(price, buying)` to compute fill with costs.
            max_bars:     Optional per-call time-stop override (bars). Engines pass
                          the strategy's holding_horizon so managed time-stops match
                          the barrier engine's max_hold exactly; None falls back to
                          the per-timeframe ``time_stop_bars`` table.

        Returns:
            (realized_pnl, exit_reason)
            - realized_pnl: PnL generated on this bar from partial/full closes.
            - exit_reason: "" if still open, else "stop", "target", "time", etc.
        """
        # Ensure TMS metadata is initialized
        self.init_position_tms(position)

        position["bars_open"] += 1
        direction = position["direction"]  # Direction enum or string
        is_long = (direction == "long" or getattr(direction, "value", "").lower() == "long")

        entry = position["entry_price"]
        units = position["units"]
        initial_units = position["initial_units"]
        stop = position["stop"]
        target = position["target"]

        # If already stopped out/closed
        if units <= 0:
            return 0.0, "closed"

        # Calculate initial risk distance for R-multiple calculations
        # In case the initial stop was different, we use the stored/initial stop.
        initial_stop = position.get("initial_stop", stop)
        risk_dist = abs(entry - initial_stop)
        if risk_dist <= 1e-8:
            risk_dist = 0.01 * entry  # Avoid divide by zero

        # Check full stop-out first (conservative).
        #
        # GAP-AWARE FILLS: a stop does not guarantee the stop PRICE. If the bar opens
        # beyond the stop — an earnings gap, a weekend crypto move — the real fill is
        # at the open, materially worse. Assuming the stop price always fills
        # understates exactly the losses that matter most (the tail), and this book
        # holds single stocks ~21 bars, so roughly one trade in four sits through an
        # earnings announcement. When ``open_`` is supplied the fill is the WORSE of
        # the stop and the open; without it the old optimistic behaviour is kept so
        # callers that cannot supply it are not silently changed.
        if is_long:
            if low <= stop:
                level = min(stop, open_) if open_ is not None else stop
                fill_price = fill_fn(level, False)
                pnl = (fill_price - entry) * units
                position["units"] = 0.0
                if open_ is not None and open_ < stop:
                    position["tms_log"].append(
                        {"action": "gap_through_stop", "stop": stop, "filled": level})
                return pnl, "stop"
        else:
            if high >= stop:
                level = max(stop, open_) if open_ is not None else stop
                fill_price = fill_fn(level, True)
                pnl = (entry - fill_price) * units
                position["units"] = 0.0
                if open_ is not None and open_ > stop:
                    position["tms_log"].append(
                        {"action": "gap_through_stop", "stop": stop, "filled": level})
                return pnl, "stop"

        # Check full target hit — SKIPPED in runner mode, where the post-P1
        # remainder rides the Chandelier trail uncapped instead of capping at a
        # fixed target. (Below 1R the target can't be reached anyway, so this only
        # bites once the trade is already in profit — exactly where we want to run.)
        if not self.runner_mode:
            if is_long:
                if high >= target:
                    fill_price = fill_fn(target, False)
                    pnl = (fill_price - entry) * units
                    position["units"] = 0.0
                    return pnl, "target"
            else:
                if low <= target:
                    fill_price = fill_fn(target, True)
                    pnl = (entry - fill_price) * units
                    position["units"] = 0.0
                    return pnl, "target"

        # Initialize tracking variables for this step
        realized_pnl = 0.0
        # Buffer is pips x pip_size; pip_size is already JPY-aware at the call
        # sites (0.01 for JPY pairs), so no per-pair multiplier hacks here.
        be_buffer = self.be_buffer_pips * pip_size

        # ----------------------------------------------------------------
        # Technique 1: Partial 1 (50 %) + Move SL to Breakeven at 1R
        # ----------------------------------------------------------------
        p1_price = (entry + self.p1_r * risk_dist) if is_long else (entry - self.p1_r * risk_dist)
        has_reached_p1 = (high >= p1_price) if is_long else (low <= p1_price)

        if not position["tms_p1"] and has_reached_p1:
            close_units = initial_units * self.p1_pct
            # Make sure we don't close more than currently held
            close_units = min(close_units, units)
            if close_units > 0:
                fill_price = fill_fn(p1_price, not is_long)
                pnl = (fill_price - entry) * close_units if is_long else (entry - fill_price) * close_units
                realized_pnl += pnl
                units -= close_units
                position["units"] = units
                position["tms_p1"] = True
                position["tms_log"].append({"action": "partial_close_50pct", "price": fill_price})

            # Move SL to breakeven
            if not position["tms_be"]:
                be_sl = (entry + be_buffer) if is_long else (entry - be_buffer)
                position["stop"] = be_sl
                position["tms_be"] = True
                position["tms_log"].append({"action": "breakeven_sl", "new_sl": be_sl})

        # ----------------------------------------------------------------
        # Technique 2: Partial 2 (25 %) + Lock 0.5R at 1.5R
        # ----------------------------------------------------------------
        p2_price = (entry + self.p2_r * risk_dist) if is_long else (entry - self.p2_r * risk_dist)
        has_reached_p2 = (high >= p2_price) if is_long else (low <= p2_price)

        # Runner mode skips Partial 2 too: the whole post-P1 remainder rides the
        # trail, rather than being trimmed to 25% at 1.5R.
        if position["tms_p1"] and not position["tms_p2"] and has_reached_p2 and not self.runner_mode:
            close_units = initial_units * self.p2_pct
            close_units = min(close_units, units)
            if close_units > 0:
                fill_price = fill_fn(p2_price, not is_long)
                pnl = (fill_price - entry) * close_units if is_long else (entry - fill_price) * close_units
                realized_pnl += pnl
                units -= close_units
                position["units"] = units
                position["tms_p2"] = True
                position["tms_log"].append({"action": "partial_close_25pct", "price": fill_price})

            # Lock 0.5R profit as new SL
            half_r = risk_dist * 0.5
            locked_sl = (entry + half_r) if is_long else (entry - half_r)
            current_stop = position["stop"]
            if (is_long and locked_sl > current_stop) or (not is_long and locked_sl < current_stop):
                position["stop"] = locked_sl
                position["tms_log"].append({"action": "lock_0.5R_sl", "new_sl": locked_sl})

        # ----------------------------------------------------------------
        # Technique 3: ATR Chandelier Trail (after partial 1)
        # ----------------------------------------------------------------
        history_len = bars_history.get("len", len(bars_history)) if isinstance(bars_history, dict) else len(bars_history)
        if position["tms_p1"] and atr > 0 and history_len >= 22:
            if isinstance(bars_history, dict):
                # Backtester pre-calculated values for performance
                swing_max = bars_history.get("high", entry)
                swing_min = bars_history.get("low", entry)
            else:
                # Live code passes list of dicts
                if is_long:
                    recent_highs = [b["high"] for b in bars_history[-22:]]
                    swing_max = max(recent_highs)
                else:
                    recent_lows = [b["low"] for b in bars_history[-22:]]
                    swing_min = min(recent_lows)

            if is_long:
                chandelier = swing_max - (self.chandelier_mult * atr)
                if chandelier > position["stop"] and chandelier < close:
                    position["stop"] = chandelier
                    position["tms_log"].append({"action": "chandelier_trail", "new_sl": chandelier})
            else:
                chandelier = swing_min + (self.chandelier_mult * atr)
                if chandelier < position["stop"] and chandelier > close:
                    position["stop"] = chandelier
                    position["tms_log"].append({"action": "chandelier_trail", "new_sl": chandelier})

        # ----------------------------------------------------------------
        # Technique 5: Volatility Squeeze (tighten trail to 1×ATR)
        # ----------------------------------------------------------------
        if position["tms_p1"] and atr > 0 and is_squeeze:
            tight_trail = atr * self.squeeze_mult
            if is_long:
                tight_sl = close - tight_trail
                if tight_sl > position["stop"] and tight_sl < close:
                    position["stop"] = tight_sl
                    position["tms_log"].append({"action": "squeeze_tighten", "new_sl": tight_sl})
            else:
                tight_sl = close + tight_trail
                if tight_sl < position["stop"] and tight_sl > close:
                    position["stop"] = tight_sl
                    position["tms_log"].append({"action": "squeeze_tighten", "new_sl": tight_sl})

        # ----------------------------------------------------------------
        # Technique 4: Time-Based Exit (kill stagnant trades)
        # ----------------------------------------------------------------
        # Per-call override wins (engines pass the strategy's holding_horizon so
        # managed time-stops match the barrier engine's max_hold); otherwise fall
        # back to the per-timeframe table.
        tf_clean = str(timeframe).lower().strip()
        if max_bars is None:
            max_bars = self.time_stop_bars.get(tf_clean, 10)
        
        # Calculate current profit in R
        current_pnl_dist = (close - entry) if is_long else (entry - close)
        current_r = current_pnl_dist / risk_dist

        if position["bars_open"] > max_bars and current_r < 0.25:
            # Stagnant trade, close the remainder in full
            fill_price = fill_fn(close, not is_long)
            pnl = (fill_price - entry) * units if is_long else (entry - fill_price) * units
            realized_pnl += pnl
            position["units"] = 0.0
            position["tms_log"].append({"action": "time_stop", "bars_open": position["bars_open"]})
            return realized_pnl, "time"

        return realized_pnl, ""
