from engine.brokers import MultiBroker, MultiMaster, adjust_config, required_secrets_for_config


class StubBroker:
    def __init__(self, label):
        self.label = label
        self.calls = []

    def history(self, symbol, resolution, day_from, day_to):
        self.calls.append(("history", symbol))
        return [self.label]

    def quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        return {s: {"lp": 1, "provider": self.label} for s in symbols}

    def probe(self):
        return True

    def stats_snapshot(self):
        return {"total": 2, "by_method": {"quotes": {"calls": 1, "ok": 1, "errors": 0, "refused": 0, "avg_ms": 10},
                                            "history": {"calls": 1, "ok": 1, "errors": 0, "refused": 0, "avg_ms": 20}}}


class StubMaster:
    def __init__(self, label):
        self.label = label
        self.calls = []

    def rows(self, name, root):
        self.calls.append(("rows", name, root))
        return [self.label]

    def futures(self, name, root):
        self.calls.append(("futures", name, root))
        return [self.label]

    def nearest_future(self, name, root, day):
        self.calls.append(("nearest", name, root))
        return {"symbol": f"{self.label}:{root}"}

    def choose_expiry(self, *args):
        self.calls.append(("expiry", args[0], args[1]))
        return (123, None)

    def future_for_expiry(self, *args):
        self.calls.append(("future_for_expiry", args[0], args[1]))
        return {"symbol": self.label}

    def pick_option(self, *args):
        self.calls.append(("pick_option", args[0], args[1]))
        return {"symbol": self.label}


def test_dual_feed_routes_market_data_by_instrument():
    kotak, fyers = StubBroker("kotak"), StubBroker("fyers")
    router = MultiBroker(kotak, fyers)
    from datetime import date
    router.history("NSE:RELIANCE-EQ", "5", date.today(), date.today())
    router.history("MCX:CRUDEOILM26NOVFUT", "5", date.today(), date.today())
    quotes = router.quotes(["NSE:RELIANCE-EQ", "MCX:CRUDEOILM26NOVFUT", "NSE:NIFTY50-INDEX"])
    assert kotak.calls[0] == ("history", "NSE:RELIANCE-EQ")
    assert fyers.calls[0] == ("history", "MCX:CRUDEOILM26NOVFUT")
    assert quotes["NSE:RELIANCE-EQ"]["provider"] == "kotak"
    assert quotes["MCX:CRUDEOILM26NOVFUT"]["provider"] == "fyers"
    assert router.probe() is True
    snap = router.stats_snapshot()
    assert snap["total"] == 4
    assert snap["by_method"]["quotes"]["calls"] == 2
    assert snap["providers"]["commodity"]["total"] == 2


def test_contract_masters_follow_the_price_provider():
    kotak, fyers = StubMaster("kotak"), StubMaster("fyers")
    master = MultiMaster(kotak, fyers)
    assert master.rows("NSE_FO", "NIFTY") == ["kotak"]
    assert master.nearest_future("MCX_COM", "CRUDEOILM", None)["symbol"] == "fyers:CRUDEOILM"
    assert master.choose_expiry("MCX_COM", "GOLDM", None, 3, "roll") == (123, None)
    assert len(kotak.calls) == 1
    assert len(fyers.calls) == 2


def test_commodity_markets_are_enabled_only_when_secondary_feed_is_selected():
    cfg = {"markets": [{"id": "CRUDE", "kind": "commodity", "enabled": True}]}
    assert adjust_config(cfg, "kotak")
    assert cfg["markets"][0]["enabled"] is False
    cfg = {"markets": [{"id": "CRUDE", "kind": "commodity", "enabled": True}]}
    assert not adjust_config(cfg, "kotak", secondary="fyers")
    assert cfg["markets"][0]["enabled"] is True
    assert required_secrets_for_config({"broker": {"primary": "kotak", "commodity_feed": "fyers"}}) == ["KOTAK_CONSUMER_KEY"]
