import { describe, expect, it } from "vitest";
import type { LedgerTrade } from "../src/generated/ledger-trade";
import { summarizeLedger } from "../src/lib/ledger";

const t = (over: Partial<LedgerTrade>): LedgerTrade => ({
  schema: 1, id: Math.random().toString(36), desk: "intraday_stocks", source: "strategy:orb", date: "2026-11-11", symbol: "NSE:X-EQ", name: "X",
  instrument: "stock", direction: 1, action: "BUY", qty: 10, entry_price: 100, entry_time: "2026-11-11T09:30:00+05:30", status: "CLOSED", net: 100, r: 1, ...over,
} as LedgerTrade);

describe("ledger summary", () => {
  it("counts wins, losses and unresolved separately and averages R over settled trades", () => {
    const rows = summarizeLedger([
      t({ net: 200, r: 2 }), t({ net: -100, r: -1 }), t({ net: -50, r: -0.5 }),
      t({ status: "UNRESOLVED", net: null, r: null }),
    ]);
    expect(rows).toEqual([{ desk: "intraday_stocks", trades: 4, wins: 1, losses: 2, unresolved: 1, avgR: 0.167, netRupees: 50 }]);
  });

  it("never shows rupees for the swing desk, whose paper trades are per share", () => {
    const [row] = summarizeLedger([t({ desk: "swing", net: 12, r: 0.4 }), t({ desk: "swing", net: -5, r: -0.2 })]);
    expect(row).toMatchObject({ desk: "swing", trades: 2, netRupees: null, avgR: 0.1 });
  });

  it("groups by desk in a stable order and survives an empty ledger", () => {
    expect(summarizeLedger([t({ desk: "mcx" }), t({ desk: "index_options" })]).map((r) => r.desk)).toEqual(["index_options", "mcx"]);
    expect(summarizeLedger([])).toEqual([]);
  });

  it("a desk with only unresolved trades has no average and no rupees", () => {
    const [row] = summarizeLedger([t({ status: "UNRESOLVED", net: null, r: null })]);
    expect(row).toMatchObject({ avgR: null, netRupees: null, unresolved: 1, trades: 1 });
  });
});
