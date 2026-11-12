"""Relative-strength leaders and market health, from NSE's daily price files: one download a day, no broker requests.

    python -m engine.leaders --bhav <folder> --out <folder>/leaders.json

The "Leader Playbook" notes (and O'Neil and Minervini before them) start every trade the same way: trade only the strongest shares, and
look at how many shares are above their long-term averages before taking risk. This computes both for the whole market after each close:
  - each share's relative strength, the percentile of its weighted 3/6/9/12-month return (weights 40/20/20/20) among every share that
    trades at least Rs 10 crore a day, as in engine/context.py, so it is the same number the research tested;
  - the leaders: strength 80 or more, price above its 200-day average, price above Rs 10, liquid;
  - market health: the share of liquid shares above their 50-day and 200-day averages, and advances against declines on the day. The
    notes call fewer than 20% above the 200-day average a bear phase.
The research (docs/STRATEGY_RESEARCH.md) found that relative strength helps a breakout but only clearly in some markets, and that the bear
gauge almost never fires in the 200 most traded shares (24 days in nine years). So the leaders are shown and used as a flag on the watchlist
and in alerts, not as a gate that blocks trades.
"""
import json
import os
from datetime import date, timedelta

from . import bhav, clock, context as cx
from .daily import Daily

FRESH_DAYS = 4                 # a list older than this many days is shown as out of date
BEAR_BELOW = 0.20


def compute(days, min_value_cr=10.0, rs_min=80.0, top=120):
    """The leaders file for the last day in `days` ({date: rows} as read by bhav.load_days). None if there is too little history."""
    candles, meta, last_day = bhav.build(days, min_value_cr=min_value_cr, min_bars=cx.CONTEXT["rs_weights"][-1][0] + 1, log=lambda m: None)
    rows = []
    for isin, c in candles.items():
        D = Daily(c, ended=meta[isin]["ended"])
        if D.d[-1] != last_day:
            continue                                         # not trading on the last day: no rating
        i = D.n - 1
        score = cx.rs_score(D, i)
        val = D.val_prev[i]
        if score is None or not val or val < min_value_cr * 1e7:
            continue
        hi52 = max(D.h[max(0, i - 252):i + 1])
        rows.append({"symbol": meta[isin]["symbol"], "score": score, "close": D.c[i], "above50": D.sma50[i] is not None and D.c[i] > D.sma50[i],
                     "above200": D.sma200[i] is not None and D.c[i] > D.sma200[i], "adv": D.c[i] > D.c[i - 1] if i else False,
                     "ret3m": D.c[i] / D.c[i - 63] - 1, "off_high": 1 - D.c[i] / hi52, "turnover_cr": val / 1e7})
    if len(rows) < 50:
        return None
    rows.sort(key=lambda r: r["score"])
    n = len(rows) - 1
    for k, r in enumerate(rows):
        r["rs"] = round(100.0 * k / n, 1)
    lead = [r for r in rows if r["rs"] >= rs_min and r["above200"] and r["close"] > 10]
    lead.sort(key=lambda r: -r["rs"])
    share = lambda key: round(sum(1 for r in rows if r[key]) / len(rows), 3)
    health = {"above50": share("above50"), "above200": share("above200"), "advancing": share("adv"), "bear_phase": share("above200") < BEAR_BELOW}
    return {
        "asof": last_day.isoformat(), "universe": len(rows), "rs_min": rs_min, "breadth": health,
        "leaders": [{"symbol": r["symbol"], "rs": r["rs"], "ret3m_pct": round(r["ret3m"] * 100, 1), "off_high_pct": round(r["off_high"] * 100, 1),
                     "turnover_cr": round(r["turnover_cr"], 1)} for r in lead[:top]],
        "n_leaders": len(lead),
        "rs": {r["symbol"]: r["rs"] for r in rows},
        "note": "Relative strength is a percentile of past performance among liquid NSE shares, not a forecast.",
    }


class Leaders:
    """What the rest of the tool asks of the leaders file."""

    def __init__(self, folder=None, data=None):
        self.path = os.path.join(folder, "leaders.json") if folder else None
        self.data = data
        if data is None and self.path:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = None

    def ok(self, today):
        if not self.data:
            return False
        try:
            return (today - date.fromisoformat(self.data["asof"])).days <= FRESH_DAYS
        except (KeyError, ValueError):
            return False

    def rs(self, name):
        """The share's relative-strength rating, or None if unknown."""
        return (self.data or {}).get("rs", {}).get(name)

    def is_leader(self, name):
        r = self.rs(name)
        return r is not None and r >= (self.data or {}).get("rs_min", 80)

    def public(self, today=None):
        d = self.data
        if not d:
            return {"ok": False}
        return {"ok": True if today is None else self.ok(today), "asof": d["asof"], "universe": d["universe"], "breadth": d["breadth"],
                "leaders": d["leaders"][:25], "n_leaders": d["n_leaders"], "rs_min": d["rs_min"]}

    def health_line(self):
        """One line for the morning brief."""
        if not self.data:
            return None
        b = self.data["breadth"]
        phase = " That is a bear phase by the playbook's rule (under 20%)." if b["bear_phase"] else ""
        return (f"Market health ({self.data['asof']}): {b['above200'] * 100:.0f}% of liquid shares above their 200-day average, "
                f"{b['above50'] * 100:.0f}% above their 50-day.{phase} {self.data['n_leaders']} relative-strength leaders.")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bhav", required=True, help="folder of NSE daily files (python -m engine.bhav)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=420, help="calendar days of files to read")
    a = ap.parse_args(argv)
    end = clock.now().date()
    days = bhav.load_days(a.bhav, end - timedelta(days=a.days), end)
    out = compute(days)
    if out is None:
        raise SystemExit("Not enough daily files to rate relative strength (need about 14 months).")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{out['asof']}: {out['universe']} liquid shares rated, {out['n_leaders']} leaders, "
          f"{out['breadth']['above200'] * 100:.0f}% above their 200-day average. Written to {a.out}")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(root, "_data", ".git")) and os.path.abspath(a.out).startswith(os.path.join(root, "_data")):
        from .store import Store
        Store(os.path.join(root, "_data"), "unused").publish(own={"leaders.json"})
        print("Published to your dashboard.")


if __name__ == "__main__":
    main()
