"""One cost model for every desk, the replay, the backtest and the dashboard.

Costs are estimates of brokerage, taxes and the gap between the price you see and the price you get. Every desk charges a
round trip as:

    per_trade + qty * (entry + exit) * (pct / 2 + slippage_pct) / 100

which is the formula the strategy simulator has always used. Options use the same formula with the figures the options scanner and
the backtest already assumed (Rs 100 a trade and 0.5 percent of the premium lost on each leg).
"""

DEFAULTS = {
    "stock_per_trade": 40.0, "stock_pct": 0.03, "stock_slippage_pct": 0.03,
    "index_per_trade": 80.0, "index_pct": 0.02, "index_slippage_pct": 0.01,
    "option_per_trade": 100.0, "option_pct": 0.0, "option_slippage_pct": 0.5,
}

KINDS = ("stock", "index", "option")


def merged(overrides=None):
    """DEFAULTS with any overrides on top. Unknown keys are ignored; missing keys fall back to the defaults."""
    out = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in DEFAULTS:
            out[key] = value
    return out


def from_config(cfg):
    """The cost table for a loaded config.yaml: strategy costs plus the backtest's option assumptions."""
    cfg = cfg or {}
    table = merged(((cfg.get("strategies") or {}).get("costs")) or {})
    bt = cfg.get("backtest") or {}
    if "option_cost_per_trade" in bt:
        table["option_per_trade"] = float(bt["option_cost_per_trade"])
    if "option_slippage_pct" in bt:
        table["option_slippage_pct"] = float(bt["option_slippage_pct"])
    return table


def round_trip(kind, qty, entry, exit_, table=None):
    """Estimated rupee cost of buying and selling `qty` units. `kind` is stock, index or option."""
    if kind not in KINDS:
        raise ValueError(f"unknown cost kind: {kind!r}")
    c = merged(table)
    return c[f"{kind}_per_trade"] + qty * (entry + exit_) * (c[f"{kind}_pct"] / 2 + c[f"{kind}_slippage_pct"]) / 100
