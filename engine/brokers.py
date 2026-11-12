"""Broker construction and deterministic multi-broker routing.

Kotak remains the primary provider for NSE/BSE. An optional Fyers secondary
provider serves MCX instruments. Trading is still paper-only: neither adapter
exposes order placement through this router.
"""
from .fyers import Fyers
from .kotak import Kotak, KotakMaster
from .symbols import Master

KOTAK_NOTE = (
    "Commodity (MCX) markets are paused: Kotak Neo does not provide historical "
    "price candles for MCX. Configure broker.commodity_feed: fyers and complete "
    "the Fyers market-data check to enable them."
)


def primary(cfg):
    return str(((cfg or {}).get("broker") or {}).get("primary") or "kotak").strip().lower()


def commodity_feed(cfg):
    return str(((cfg or {}).get("broker") or {}).get("commodity_feed") or "").strip().lower()


def label(kind):
    return "Kotak Neo" if kind == "kotak" else "Fyers"


def required_secrets(kind):
    return ["KOTAK_CONSUMER_KEY"] if kind == "kotak" else ["FYERS_APP_ID", "FYERS_SECRET_KEY"]


def required_secrets_for_config(cfg):
    # Secondary-provider credentials are optional: missing Fyers must not stop
    # the primary Kotak equity/index scanner. MCX is then marked unavailable.
    return list(required_secrets(primary(cfg)))


def secret_material(env):
    """Keep Fyers-encrypted session material readable across a broker migration."""
    return env.get("FYERS_SECRET_KEY") or env.get("KOTAK_CONSUMER_KEY") or ""


def adjust_config(cfg, kind, secondary=None):
    """Disable only instruments for which no configured price-history source exists."""
    notes = []
    has_fyers_mcx = kind == "fyers" or (kind == "kotak" and secondary == "fyers")
    if kind == "kotak" and not has_fyers_mcx:
        paused = [m for m in cfg.get("markets", [])
                  if m.get("kind") == "commodity" and m.get("enabled", True)]
        for market in paused:
            market["enabled"] = False
        if paused:
            notes.append(KOTAK_NOTE)
    return notes


class MultiBroker:
    """Route MCX-prefixed symbols to Fyers and all remaining symbols to Kotak.

    The router deliberately does not silently fall back from a failed Fyers MCX
    request to Kotak: Kotak's missing MCX history would only hide the real fault.
    """
    name = "multi"
    label = "Kotak Neo + Fyers"
    can_login = False

    def __init__(self, primary_client, secondary_client, primary_kind="kotak"):
        self.primary = primary_client
        self.secondary = secondary_client
        self.primary_kind = primary_kind

    @staticmethod
    def _is_mcx(symbol):
        s = str(symbol).upper()
        return s.startswith("MCX:") or s.startswith("MCX_COM:")

    def _for_symbol(self, symbol):
        if self._is_mcx(symbol):
            return self.secondary
        return self.primary

    def history(self, symbol, resolution, day_from, day_to):
        return self._for_symbol(symbol).history(symbol, resolution, day_from, day_to)

    def quotes(self, symbols):
        routed = {}
        for symbol in symbols:
            client = self._for_symbol(symbol)
            routed.setdefault(client, []).append(symbol)
        out = {}
        for client, names in routed.items():
            out.update(client.quotes(names))
        return out

    def probe(self):
        return bool(self.primary.probe())

    def probe_secondary(self):
        return bool(self.secondary and self.secondary.probe())

    def stats_snapshot(self):
        primary = self.primary.stats_snapshot() if hasattr(self.primary, "stats_snapshot") else {}
        secondary = self.secondary.stats_snapshot() if self.secondary and hasattr(self.secondary, "stats_snapshot") else {}
        by_method = {}
        for label, snap in (("primary", primary), ("commodity", secondary)):
            for method, row in (snap.get("by_method") or {}).items():
                dst = by_method.setdefault(method, {"calls": 0, "ok": 0, "errors": 0, "refused": 0, "avg_ms": 0})
                for key in ("calls", "ok", "errors", "refused"):
                    dst[key] += int(row.get(key, 0) or 0)
                # Weighted average is reconstructed from each provider's count.
                dst["avg_ms"] += int(row.get("avg_ms", 0) or 0) * int(row.get("calls", 0) or 0)
        for row in by_method.values():
            row["avg_ms"] = round(row["avg_ms"] / row["calls"]) if row["calls"] else 0
        return {
            "since": primary.get("since") or secondary.get("since"),
            "total": int(primary.get("total", 0) or 0) + int(secondary.get("total", 0) or 0),
            "by_method": by_method,
            "history_rate": primary.get("history_rate", 0),
            "cooling": primary.get("cooling", 0),
            "providers": {"primary": primary, "commodity": secondary},
        }


class MultiMaster:
    """Resolve contract metadata from the same broker that supplies its prices."""
    def __init__(self, primary_master, commodity_master):
        self.primary_master = primary_master
        self.commodity_master = commodity_master

    def _master(self, name):
        return self.commodity_master if str(name).upper() in {"MCX_COM", "MCX_FO"} else self.primary_master

    def rows(self, name, root):
        return self._master(name).rows(name, root)

    def futures(self, name, root):
        return self._master(name).futures(name, root)

    def nearest_future(self, name, root, day):
        return self._master(name).nearest_future(name, root, day)

    def choose_expiry(self, name, root, day, min_days, near):
        return self._master(name).choose_expiry(name, root, day, min_days, near)

    def future_for_expiry(self, name, root, expiry, day):
        return self._master(name).future_for_expiry(name, root, expiry, day)

    def pick_option(self, name, root, expiry, price, cp, choice="ATM"):
        return self._master(name).pick_option(name, root, expiry, price, cp, choice)


def build(cfg, env):
    """Return (price source, contract master). Backwards-compatible single broker by default."""
    bc = (cfg.get("broker") or {})
    kind = primary(cfg)
    if kind == "kotak":
        kc = (bc.get("kotak") or {})
        kotak = Kotak(env["KOTAK_CONSUMER_KEY"].strip(), environment=kc.get("environment", "prod"),
                      index_tokens=kc.get("index_tokens"))
        kotak_master = KotakMaster(kotak)
        if commodity_feed(cfg) == "fyers":
            fyers = Fyers(env.get("FYERS_APP_ID", "").strip(), env.get("FYERS_SECRET_KEY", "").strip())
            return MultiBroker(kotak, fyers, "kotak"), MultiMaster(kotak_master, Master())
        return kotak, kotak_master
    if kind == "fyers":
        fyers = Fyers(env["FYERS_APP_ID"].strip(), env["FYERS_SECRET_KEY"].strip())
        return fyers, Master()
    raise ValueError(f"broker: primary must be kotak or fyers, not {kind!r}")
