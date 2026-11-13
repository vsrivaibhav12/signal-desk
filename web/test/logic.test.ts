import { describe, expect, it } from "vitest";
import { checkSummary, effectiveIdea, mergeIdeas, orderTicket, rankIdeas } from "../src/lib/ideas";
import { clockTime, inr, price, safeHttpUrl, signedInr, signedR } from "../src/lib/format";
import { swingSize } from "../src/lib/sizing";
import { ageSeconds, freshness, humanAge } from "../src/lib/time";
import { assessTrust } from "../src/lib/trust";
import { iso, makeIdea, makeLive, NOW } from "./helpers";

describe("time", () => {
  it("ages are seconds in the past, null when there is no usable time", () => {
    expect(ageSeconds(NOW, iso(90))).toBe(90);
    expect(ageSeconds(NOW, null)).toBeNull();
    expect(ageSeconds(NOW, "not a date")).toBeNull();
  });

  it("freshness uses the published thresholds, and a far-future time is never fresh", () => {
    expect(freshness(10)).toBe("fresh");
    expect(freshness(299)).toBe("fresh");
    expect(freshness(300)).toBe("amber");
    expect(freshness(599)).toBe("amber");
    expect(freshness(600)).toBe("stale");
    expect(freshness(-30)).toBe("fresh");           // a little clock skew is tolerated
    expect(freshness(-3600)).toBe("unknown");       // an hour in the future is not believable
    expect(freshness(null)).toBe("unknown");
    expect(freshness(450, { price_amber_s: 60, price_stale_s: 120, idea_expire_s: 900 })).toBe("stale");
  });

  it("reads ages in plain words", () => {
    expect(humanAge(5)).toBe("just now");
    expect(humanAge(180)).toBe("3 min ago");
    expect(humanAge(3900)).toBe("1 h 05 min ago");
    expect(humanAge(3 * 86400)).toBe("3 d ago");
    expect(humanAge(null)).toBe("time unknown");
    expect(humanAge(-9999)).toBe("time unknown");
  });
});

describe("trust", () => {
  it("is ok when the engine is watching and everything is recent", () => {
    const t = assessTrust(makeLive(), NOW);
    expect(t.state).toBe("ok");
    expect(t.reasons).toEqual([]);
  });

  it("nothing received is offline", () => {
    expect(assessTrust(undefined, NOW).state).toBe("offline");
  });

  it("a session that is not running is closed, not stale, however old the numbers are", () => {
    const live = makeLive({ session: { phase: "resting", running: false }, generated_at: iso(6 * 3600), freshness: { prices_asof: iso(7 * 3600), last_candle_end: null, quotes_scan_asof: null, desk_updated: null } });
    expect(assessTrust(live, NOW).state).toBe("closed");
  });

  describe("a session that should be running but is not", () => {
    const windows = [{ name: "day", start: "08:30", end: "15:23" }, { name: "evening", start: "17:15", end: "22:38" }];
    const resting = (over = {}) => makeLive({ session: { phase: "resting", running: false }, generated_at: iso(18 * 3600), session_windows: windows, ...over });

    it("is a fault, not a closed market, when the engine last published long ago", () => {
      const t = assessTrust(resting(), NOW);                       // NOW is a Wednesday, 10:00 India time
      expect(t.state).toBe("stale");
      expect(t.reasons[0]).toMatch(/should be running now \(day session, 08:30 to 15:23/);
      expect(t.reasons[0]).toMatch(/Start the Trade engine|start the Trade engine/);
    });

    it("is simply closed between sessions, at weekends, and when the engine finished moments ago", () => {
      const sixThirty = Date.parse("2026-11-11T16:30:00+05:30");
      expect(assessTrust(resting(), sixThirty).state).toBe("closed");
      const saturday = Date.parse("2026-11-14T10:00:00+05:30");
      expect(assessTrust(resting(), saturday).state).toBe("closed");
      expect(assessTrust(resting({ generated_at: iso(60) }), NOW).state).toBe("closed");
    });

    it("an older file with no session windows keeps the old behaviour", () => {
      expect(assessTrust(resting({ session_windows: undefined }), NOW).state).toBe("closed");
    });

    it("a starting engine is degraded while fresh and stale if it never got going", () => {
      expect(assessTrust(makeLive({ session: { phase: "starting", running: true }, generated_at: iso(40), session_windows: windows }), NOW).state).toBe("degraded");
      expect(assessTrust(makeLive({ session: { phase: "starting", running: true }, generated_at: iso(3000), session_windows: windows }), NOW).state).toBe("stale");
    });

    it("works out the window in India time whatever the viewer's own time zone", () => {
      const evening = Date.parse("2026-11-11T20:00:00+05:30");
      expect(assessTrust(resting(), evening).reasons[0]).toMatch(/evening session, 17:15 to 22:38/);
      expect(assessTrust(resting(), Date.parse("2026-11-11T08:29:00+05:30")).state).toBe("closed");
      expect(assessTrust(resting(), Date.parse("2026-11-11T08:31:00+05:30")).state).toBe("stale");
    });
  });

  it("an engine that says it is watching but has stopped publishing is stale", () => {
    const t = assessTrust(makeLive({ generated_at: iso(1200) }), NOW);
    expect(t.state).toBe("stale");
    expect(t.reasons[0]).toMatch(/has not published/);
  });

  it("old prices are stale even when the file itself is fresh", () => {
    const t = assessTrust(makeLive({ freshness: { prices_asof: iso(900), last_candle_end: null, quotes_scan_asof: null, desk_updated: null } }), NOW);
    expect(t.state).toBe("stale");
  });

  it("an unknown price time is stale, never fresh", () => {
    expect(assessTrust(makeLive({ freshness: { prices_asof: null, last_candle_end: null, quotes_scan_asof: null, desk_updated: null } }), NOW).state).toBe("stale");
  });

  it("the engine's own WATCH decision and ageing prices make it degraded", () => {
    const watch = assessTrust(makeLive({ health: { decision: "WATCH", asof: iso(10), blocking: ["Nifty 50 candles"], items: [] } }), NOW);
    expect(watch.state).toBe("degraded");
    expect(watch.reasons[0]).toMatch(/Nifty 50 candles/);
    const amber = assessTrust(makeLive({ freshness: { prices_asof: iso(400), last_candle_end: null, quotes_scan_asof: null, desk_updated: null } }), NOW);
    expect(amber.state).toBe("degraded");
  });

  it("waiting for a login is degraded", () => {
    expect(assessTrust(makeLive({ session: { phase: "login", running: true } }), NOW).state).toBe("degraded");
  });
});

describe("what an idea means right now", () => {
  const ok = assessTrust(makeLive(), NOW);

  it("a fresh TAKE in good conditions is actionable", () => {
    const e = effectiveIdea(makeIdea(), ok, NOW);
    expect(e).toMatchObject({ grade: "take", actionable: true, reasons: [] });
  });

  it("a TAKE older than the expiry window is expired, not a TAKE", () => {
    const e = effectiveIdea(makeIdea({ signaled_at: iso(1000) }), ok, NOW);
    expect(e.grade).toBe("expired");
    expect(e.actionable).toBe(false);
    expect(e.reasons[0]).toMatch(/moved on/);
  });

  it("a TAKE with no usable time is expired", () => {
    expect(effectiveIdea(makeIdea({ signaled_at: null }), ok, NOW).grade).toBe("expired");
  });

  it("stale data expires every TAKE, whatever its own age", () => {
    const stale = assessTrust(makeLive({ generated_at: iso(1500) }), NOW);
    const e = effectiveIdea(makeIdea({ signaled_at: iso(10) }), stale, NOW);
    expect(e.grade).toBe("expired");
    expect(e.actionable).toBe(false);
  });

  it("degraded data shows a TAKE as WATCH", () => {
    const degraded = assessTrust(makeLive({ health: { decision: "WATCH", asof: iso(1), blocking: ["Quotes scan"], items: [] } }), NOW);
    const e = effectiveIdea(makeIdea(), degraded, NOW);
    expect(e.grade).toBe("watch");
    expect(e.actionable).toBe(false);
  });

  it("a closed session keeps the grade but is never actionable", () => {
    const closed = assessTrust(makeLive({ session: { phase: "resting", running: false } }), NOW);
    const e = effectiveIdea(makeIdea({ signaled_at: iso(7200) }), closed, NOW);
    expect(e).toMatchObject({ grade: "take", actionable: false });
  });

  it("the page can lower a grade but never raise one", () => {
    for (const grade of ["watch", "wait"] as const) {
      const e = effectiveIdea(makeIdea({ grade }), ok, NOW);
      expect(e.grade).toBe(grade);
      expect(e.actionable).toBe(false);
    }
  });

  it("swing setups are actionable until their close is old, then expire", () => {
    const setup = makeIdea({ desk: "swing", grade: "setup", signaled_at: iso(3600) });
    expect(effectiveIdea(setup, ok, NOW)).toMatchObject({ grade: "setup", actionable: true });
    expect(effectiveIdea({ ...setup, signaled_at: iso(5 * 86400) }, ok, NOW).grade).toBe("expired");
  });

  it("a swing setup is not demoted by intraday data staleness", () => {
    const stale = assessTrust(makeLive({ generated_at: iso(1500) }), NOW);
    expect(effectiveIdea(makeIdea({ desk: "swing", grade: "setup", signaled_at: iso(3600) }), stale, NOW).grade).toBe("setup");
  });
});

describe("ranking and merging", () => {
  const ok = assessTrust(makeLive(), NOW);

  it("actionable ideas come first, expired last, then by quality and recency", () => {
    const items = mergeIdeas([[
      makeIdea({ id: "old", signaled_at: iso(2000), quality: 99 }),
      makeIdea({ id: "watch", grade: "watch", quality: 90 }),
      makeIdea({ id: "low", quality: 50 }),
      makeIdea({ id: "high", quality: 70 }),
      makeIdea({ id: "high-newer", quality: 70, signaled_at: iso(20) }),
    ]], ok, NOW);
    expect(items.map((i) => i.idea.id)).toEqual(["high-newer", "high", "low", "watch", "old"]);
  });

  it("an idea in two lists appears once, the first copy winning", () => {
    const items = mergeIdeas([[makeIdea({ id: "x", name: "first" })], [makeIdea({ id: "x", name: "second" })]], ok, NOW);
    expect(items).toHaveLength(1);
    expect(items[0]!.idea.name).toBe("first");
  });

  it("rankIdeas does not change its input", () => {
    const a = { idea: makeIdea({ id: "a", quality: 10 }), eff: effectiveIdea(makeIdea(), ok, NOW) };
    const b = { idea: makeIdea({ id: "b", quality: 90 }), eff: effectiveIdea(makeIdea(), ok, NOW) };
    const input = [a, b];
    rankIdeas(input);
    expect(input[0]).toBe(a);
  });
});

describe("checks and the order ticket", () => {
  it("summarises checks without counting those that do not apply", () => {
    const c = makeIdea().checks;
    expect(checkSummary(c).text).toBe("4 of 4 checks passed");
    expect(checkSummary([{ id: "a", label: "A", state: "pass" }, { id: "b", label: "B", state: "unknown" }, { id: "c", label: "C", state: "fail" }, { id: "d", label: "D", state: "na" }]).text)
      .toBe("1 of 3 checks passed, 1 failed, 1 not verified");
    expect(checkSummary([]).text).toBe("");
    expect(checkSummary([{ id: "a", label: "A", state: "na" }]).text).toBe("");
  });

  it("the ticket carries the numbers and says nothing is sent", () => {
    const idea = makeIdea();
    const text = orderTicket(idea, effectiveIdea(idea, assessTrust(makeLive(), NOW), NOW), NOW);
    expect(text).toContain("BUY HAL (NSE:HAL-EQ)");
    expect(text).toContain("Paper entry about 4,000.00 · Stop 3,960.00 · Target 4,080.00");
    expect(text).toContain("Paper size 12 · Risk ₹480");
    expect(text).toContain("Grade: TAKE (4 of 4 checks passed)");
    expect(text).toContain("2 min ago");
    expect(text).toMatch(/Check the live price/);
  });

  it("an option ticket names the contract, and a swing ticket describes tomorrow's open", () => {
    const opt = makeIdea({ contract: "NIFTY 25000 CE", symbol: "NSE_FO|9", name: "Nifty 50 CE", target: null });
    const ot = orderTicket(opt, effectiveIdea(opt, assessTrust(makeLive(), NOW), NOW), NOW);
    expect(ot.split("\n")[0]).toBe("BUY NIFTY 25000 CE");
    expect(ot).not.toContain("Target");
    const sw = makeIdea({ desk: "swing", grade: "setup", entry: 512.4, qty: null, risk: null, stop: 471.4, target: null });
    const st = orderTicket(sw, effectiveIdea(sw, assessTrust(makeLive(), NOW), NOW), NOW);
    expect(st).toContain("buy at tomorrow's open");
    expect(st).not.toContain("Paper size");
  });

  it("a demoted idea's ticket opens with the reason", () => {
    const idea = makeIdea({ signaled_at: iso(2000) });
    expect(orderTicket(idea, effectiveIdea(idea, assessTrust(makeLive(), NOW), NOW), NOW)).toMatch(/Note: Signalled .* moved on/);
  });
});

describe("swing sizing", () => {
  it("sizes so that hitting the stop loses the chosen share of capital", () => {
    const r = swingSize({ capital: 500000, riskPct: 0.5, entry: 500, stopPct: 8 });
    expect(r.qty).toBe(62);                       // 2,500 of risk / 40 per share = 62.5, rounded down
    expect(r.risk).toBe(2480);
    expect(r.value).toBe(31000);
    expect(r.limitedByCapital).toBe(false);
  });

  it("never spends more than the capital", () => {
    const r = swingSize({ capital: 10000, riskPct: 50, entry: 500, stopPct: 8 });
    expect(r.qty).toBe(20);
    expect(r.limitedByCapital).toBe(true);
  });

  it("invalid input gives zero shares, not a guess", () => {
    for (const bad of [{ capital: 0 }, { riskPct: -1 }, { entry: NaN }, { stopPct: 0 }, { stopPct: 100 }, { riskPct: 101 }]) {
      expect(swingSize({ capital: 100000, riskPct: 1, entry: 100, stopPct: 8, ...bad }).qty).toBe(0);
    }
  });

  it("a share priced above the risk budget is zero shares", () => {
    expect(swingSize({ capital: 100000, riskPct: 0.1, entry: 5000, stopPct: 8 }).qty).toBe(0);
  });
});

describe("formatting", () => {
  it("uses Indian grouping and shows a dash for missing numbers", () => {
    expect(inr(1234567)).toBe("₹12,34,567");
    expect(inr(-480)).toBe("-₹480");
    expect(inr(null)).toBe("–");
    expect(inr(NaN)).toBe("–");
    expect(signedInr(120)).toBe("+₹120");
    expect(signedInr(-120)).toBe("-₹120");
    expect(signedInr(0)).toBe("₹0");
    expect(price(4000)).toBe("4,000.00");
    expect(signedR(-0.114)).toBe("-0.11R");
    expect(signedR(0.5)).toBe("+0.50R");
  });

  it("shows India time", () => {
    expect(clockTime("2026-11-11T09:55:00+05:30")).toBe("9:55 am");
    expect(clockTime(null)).toBe("–");
  });

  it("only http and https links become links", () => {
    expect(safeHttpUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,x")).toBeNull();
    expect(safeHttpUrl(42)).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
  });
});
