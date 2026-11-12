// Summaries of the permanent ledger of settled paper trades.
import type { LedgerTrade } from "../generated/ledger-trade";

export interface DeskSummary {
  desk: string;
  trades: number;
  wins: number;
  losses: number;
  unresolved: number;
  /** Mean R after estimated costs over trades that have one; null when none do. */
  avgR: number | null;
  /** Rupees after estimated costs. Null for the swing desk, whose paper trades are measured per share, not in rupees. */
  netRupees: number | null;
}

const round = (n: number, d: number) => Math.round(n * 10 ** d) / 10 ** d;

export function summarizeLedger(trades: LedgerTrade[]): DeskSummary[] {
  const by = new Map<string, LedgerTrade[]>();
  for (const t of trades) by.set(t.desk, [...(by.get(t.desk) ?? []), t]);
  return [...by.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([desk, list]) => {
    const settled = list.filter((t) => t.status === "CLOSED" && t.net != null);
    const rs = settled.map((t) => t.r).filter((r): r is number => typeof r === "number" && Number.isFinite(r));
    return {
      desk,
      trades: list.length,
      wins: settled.filter((t) => (t.net as number) > 0).length,
      losses: settled.filter((t) => (t.net as number) < 0).length,
      unresolved: list.filter((t) => t.status === "UNRESOLVED").length,
      avgR: rs.length ? round(rs.reduce((a, b) => a + b, 0) / rs.length, 3) : null,
      netRupees: desk === "swing" || !settled.length ? null : round(settled.reduce((a, t) => a + (t.net as number), 0), 2),
    };
  });
}
