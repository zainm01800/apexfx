"""Determinism check for the vol-adaptive-partial gate: run 1 vs run 2 must be byte-identical
modulo `generated_at` and the ledger pre-state (`n_trials_before`; the rerun dedups
271 -> 271, so n_trials_used and every DSR must match exactly)."""
import json
import sys


def strip_volatile(o):
    if isinstance(o, dict):
        return {k: strip_volatile(v) for k, v in o.items()
                if k not in ("generated_at", "n_trials_before")}
    if isinstance(o, list):
        return [strip_volatile(v) for v in o]
    return o


a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
sa, sb = strip_volatile(a), strip_volatile(b)
if sa != sb:
    # locate the first divergence for a useful error message
    def walk(x, y, path=""):
        if type(x) is not type(y):
            return path
        if isinstance(x, dict):
            if set(x) != set(y):
                return f"{path} (keys: {sorted(set(x) ^ set(y))})"
            for k in x:
                r = walk(x[k], y[k], f"{path}.{k}")
                if r:
                    return r
        elif isinstance(x, list):
            if len(x) != len(y):
                return f"{path} (len {len(x)} vs {len(y)})"
            for i, (xi, yi) in enumerate(zip(x, y)):
                r = walk(xi, yi, f"{path}[{i}]")
                if r:
                    return r
        elif x != y:
            return f"{path}: {x!r} != {y!r}"
        return None
    print("DETERMINISM FAILED, first divergence at:", walk(sa, sb))
    sys.exit(1)
print("DETERMINISM OK: identical modulo generated_at and n_trials_before")
print(f"  run1 n_trials_before={a['n_trials_before']} used={a['n_trials_used']} | "
      f"run2 n_trials_before={b['n_trials_before']} used={b['n_trials_used']}")
