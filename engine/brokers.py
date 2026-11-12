"""Chooses where prices come from. Kotak Neo is the default; Fyers still works if config.yaml says so.

    broker:
      primary: kotak        # or fyers

This tool only ever reads prices. Neither broker is used to place an order.
"""
from .fyers import Fyers
from .kotak import Kotak, KotakMaster
from .symbols import Master

KOTAK_NOTE = ("Commodity (MCX) markets are paused: Kotak Neo does not provide price history for them. "
              "Set  broker: primary: fyers  in config.yaml to trade them.")


def primary(cfg):
    return str(((cfg or {}).get("broker") or {}).get("primary") or "kotak").strip().lower()


def label(kind):
    return "Kotak Neo" if kind == "kotak" else "Fyers"


def required_secrets(kind):
    return ["KOTAK_CONSUMER_KEY"] if kind == "kotak" else ["FYERS_APP_ID", "FYERS_SECRET_KEY"]


def secret_material(env):
    """What the private file is locked with. An existing Fyers key is kept so earlier saved data stays readable."""
    return env.get("FYERS_SECRET_KEY") or env.get("KOTAK_CONSUMER_KEY") or ""


def adjust_config(cfg, kind):
    """Switches off what this price source cannot serve. Returns notes to show the user."""
    notes = []
    if kind == "kotak":
        paused = [m for m in cfg.get("markets", []) if m.get("kind") == "commodity" and m.get("enabled", True)]
        for m in paused:
            m["enabled"] = False
        if paused:
            notes.append(KOTAK_NOTE)
    return notes


def build(cfg, env):
    """(price source, contract master). Raises SystemExit-friendly KeyError if a secret is missing."""
    kind = primary(cfg)
    if kind == "kotak":
        bc = ((cfg.get("broker") or {}).get("kotak") or {})
        k = Kotak(env["KOTAK_CONSUMER_KEY"].strip(), environment=bc.get("environment", "prod"),
                  index_tokens=bc.get("index_tokens"))
        return k, KotakMaster(k)
    if kind == "fyers":
        return Fyers(env["FYERS_APP_ID"], env["FYERS_SECRET_KEY"]), Master()
    raise ValueError(f"broker: primary must be kotak or fyers, not {kind!r}")
