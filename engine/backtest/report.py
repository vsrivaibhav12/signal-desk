"""Turns a list of backtest trades into the numbers and plain-language findings."""
import math
from collections import defaultdict

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def wilson(w, n, z=1.96):
    if n == 0:
        return None
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - m) / d * 100, 1), round((c + m) / d * 100, 1)]


def summarize(trades, key="net"):
    n = len(trades)
    if not n:
        return {"n": 0}
    wins = [t for t in trades if t[key] > 0]
    losses = [t for t in trades if t[key] < 0]
    gw, gl = sum(t[key] for t in wins), -sum(t[key] for t in losses)
    eq = peak = dd = 0.0
    streak = worst_streak = 0
    for t in trades:
        eq += t[key]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if t[key] < 0 else 0
        worst_streak = max(worst_streak, streak)
    decided = len(wins) + len(losses)
    days = defaultdict(float)
    for t in trades:
        days[t["date"]] += t[key]
    rkey = "net_r" if key == "net" else "r"
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / decided * 100, 1) if decided else None,
        "win_rate_range": wilson(len(wins), decided),
        "pnl": round(eq, 2), "avg_win": round(gw / len(wins), 2) if wins else 0,
        "avg_loss": round(gl / len(losses), 2) if losses else 0,
        "profit_factor": round(gw / gl, 2) if gl else None,
        "per_trade": round(eq / n, 2), "avg_r": round(sum(t[rkey] for t in trades) / n, 3),
        "max_drawdown": round(dd, 2), "losing_streak": worst_streak,
        "best_day": round(max(days.values()), 2), "worst_day": round(min(days.values()), 2),
        "days_traded": len(days), "green_days": sum(1 for v in days.values() if v > 0),
    }


def group(trades, keyfn, order=None):
    g = defaultdict(list)
    for t in trades:
        g[keyfn(t)].append(t)
    keys = order or sorted(g, key=lambda k: -len(g[k]))
    return [dict(name=k, **summarize(g[k])) for k in keys if k in g]


def market_of(t):
    return "Stocks" if t["book"] == "Stocks" else t["name"]


def month_of(t):
    return f"{MONTHS[int(t['date'][5:7]) - 1]} {t['date'][2:4]}"


def underlying_check(trades):
    """How often the market itself moved in the trade's direction. Free of any option-price model."""
    idx = [t for t in trades if t["kind"] == "option"]
    if not idx:
        return None
    good = sum(1 for t in idx if t["u_pct"] > 0)
    return {"n": len(idx), "hit_rate": round(good / len(idx) * 100, 1),
            "avg_pct": round(sum(t["u_pct"] for t in idx) / len(idx), 3)}


def verdict(main, luck):
    if main["n"] < 30:
        return (f"Only {main['n']} trades were triggered, which is too few to say anything useful. "
                "A longer history is needed.")
    parts = []
    net, gross = main["pnl"], main["gross_pnl"]
    if net > 0:
        parts.append(f"After estimated costs the rules made a net ₹{net:,.0f} over {main['n']} trades "
                     f"({main['win_rate']}% winners).")
    else:
        parts.append(f"After estimated costs the rules lost a net ₹{-net:,.0f} over {main['n']} trades "
                     f"({main['win_rate']}% winners).")
    if gross > 0 and net <= 0:
        parts.append(f"Before costs they were ahead by ₹{gross:,.0f}, so trading costs took all of it.")
    lo, hi = main["win_rate_range"]
    parts.append(f"With this many trades the true win rate could plausibly be anywhere from {lo}% to {hi}%.")
    if luck:
        if luck["read"] == "inside":
            parts.append("Luck check: the same rules run on shuffled prices, which have no real pattern, did about as well "
                         "before costs, so these results show no sign of a real edge.")
        elif luck["read"] == "above":
            parts.append("Luck check: before costs the rules did better than every shuffled-price run, which is a "
                         "hopeful sign but not proof. It needs a longer history to confirm.")
        else:
            parts.append("Luck check: the rules did worse before costs than shuffled prices did, so there is no sign of an edge.")
    if main["n"] < 300:
        parts.append("This is a short sample, so treat it as an early reading, not a verdict.")
    return " ".join(parts)
