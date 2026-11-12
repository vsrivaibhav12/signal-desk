"""What the tool's own research says about each source of ideas, shown next to every idea so a TAKE never reads as "profitable".

These are static summaries of docs/STRATEGY_RESEARCH.md as of the date below. They are not computed live. When the research is re-run,
update the numbers here and the date. Nothing in the tool has shown a reliable after-cost edge.
"""

ASOF = "2026-11-12"
SOURCE = "docs/STRATEGY_RESEARCH.md"

EVIDENCE = {
    "orb": {"headline": "Replay, after costs: about -0.11R per trade on 644 signals graded take. No edge shown.",
            "avg_r": -0.11, "trades": 644},
    "drive": {"headline": "Replay, after costs: about -0.06R per trade on 190 signals graded take. No edge shown.",
              "avg_r": -0.06, "trades": 190},
    "ema_pullback": {"headline": "Replay: +0.10R on 77 signals graded take, but +0.44R in the first half and -0.24R in the second. Unstable.",
                     "avg_r": 0.10, "trades": 77},
    "options": {"headline": "Index and commodity option premiums have not been replayed historically, so there is no measured after-cost record. "
                            "Live quotes are being captured to build one.",
                "avg_r": None, "trades": 0},
    "swing": {"headline": "Unproven: the sealed holdout gave +0.08R over 127 trades (range -0.26 to +0.44), indistinguishable from zero.",
              "avg_r": 0.08, "trades": 127},
    "system": {"headline": "The full intraday system averaged -0.14R per trade after costs in the point-in-time replay (243 trades, range -0.24 to -0.02).",
               "avg_r": -0.14, "trades": 243},
}


def for_source(source):
    """The evidence record for an idea's source label (for example 'strategy:orb', 'rulebook', 'options_scan', 'swing')."""
    source = source or ""
    if source.startswith("strategy:"):
        key = source.split(":", 1)[1]
    elif source in ("rulebook", "options_scan"):
        key = "options"
    elif source == "swing":
        key = "swing"
    else:
        return None
    record = EVIDENCE.get(key)
    if record is None:
        return None
    return {"headline": record["headline"], "avg_r": record["avg_r"], "trades": record["trades"], "asof": ASOF, "source": SOURCE}
