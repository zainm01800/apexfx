"""V33 frozen prior-close cost screen; no current-bar or future prices."""
from math import isfinite


def cost_to_stop(prior_close, prior_atr):
    price, atr = float(prior_close), float(prior_atr)
    if not isfinite(price) or not isfinite(atr) or price <= 0 or atr <= 0:
        raise ValueError("V33 requires positive finite prior close and ATR")
    distance = 2.5 * atr
    stop = price - distance
    if stop <= 0:
        return float("inf")
    return (.001 * price + .001 * stop * .999 + .001 * stop
            + price * .02 * 7 / 365) / distance


def filter_scores(scores, prior_closes, prior_atrs):
    return {s: float(v) for s, v in scores.items()
            if float(v) > 0 and cost_to_stop(prior_closes[s], prior_atrs[s]) <= .10}


def select_cost_aware(scores, held, prior_closes, prior_atrs):
    eligible = filter_scores({s: v for s, v in scores.items() if s not in held},
                             prior_closes, prior_atrs)
    return sorted(eligible.items(), key=lambda row: (-row[1], row[0]))[:max(0, 4-len(held))]
