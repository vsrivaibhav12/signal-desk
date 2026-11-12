import { dash, price, signedInr, signedR } from "../lib/format";
import { ageSeconds, freshness, humanAge } from "../lib/time";
import { DEFAULT_THRESHOLDS } from "../lib/time";
import { deskName, Meter, pnlClass, Pill } from "../components/ui";
import { RiskCard } from "../components/Rails";
import type { AppState } from "../state";

export function Positions({ app, openable, onOpen }: { app: AppState; openable: Set<string>; onOpen: (id: string) => void }) {
  const live = app.snap.live;
  if (!live) return <div class="empty">Loading…</div>;
  const th = live.thresholds ?? DEFAULT_THRESHOLDS;
  return (
    <div class="cols">
      <div class="stack">
        <section class="card">
          <div class="card-head"><h2>Open paper positions</h2><span class="muted small">{live.positions.length} open</span></div>
          {live.positions.length ? (
            <div class="tablewrap">
              <table>
                <thead><tr><th>Position</th><th>Desk</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Last</th><th class="r">Stop</th><th class="r">Open P&amp;L</th><th class="r">R</th><th>Price</th><th>Chart</th></tr></thead>
                <tbody>
                  {live.positions.map((p) => {
                    const age = ageSeconds(app.nowMs, p.last_update);
                    const f = p.quote_status === "STALE" ? "stale" : freshness(age, th);
                    const unmonitored = p.quote_status === "STALE";
                    return (
                      <tr key={p.id}>
                        <td><b>{p.name}</b> <span class={`side ${p.action === "BUY" ? "buy" : "sell"}`}>{p.action}</span>{p.contract && <div class="muted small">{p.contract}</div>}</td>
                        <td class="small">{deskName(live, p.desk)}</td>
                        <td class="r">{p.qty}</td>
                        <td class="r">{price(p.entry_price)}</td>
                        <td class="r">{price(p.last_price)}</td>
                        <td class="r">{price(p.stop)}</td>
                        <td class={`r ${pnlClass(p.open_pnl)}`}>{signedInr(p.open_pnl)}</td>
                        <td class={`r ${pnlClass(p.open_r)}`}>{signedR(p.open_r)}</td>
                        <td>
                          <Pill tone={f === "fresh" ? "fresh" : f === "amber" ? "amber" : f === "stale" ? "stale" : "unknown"}>
                            {unmonitored ? "Unmonitored" : humanAge(age)}
                          </Pill>
                          {unmonitored && <div class="reason small">The stop cannot be verified: no fresh quote.</div>}
                        </td>
                        <td>
                          <button type="button" class="iconbtn" disabled={!openable.has(p.id)} onClick={() => onOpen(p.id)}
                            aria-label={`Chart and plan for ${p.name}`}>Chart</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <div class="empty">No paper positions are open.</div>}
        </section>

        <section class="card">
          <div class="card-head"><h2>Ended today</h2><span class="muted small">{live.ended.length} settled</span></div>
          {live.ended.length ? (
            <div class="tablewrap">
              <table>
                <thead><tr><th>Position</th><th>Desk</th><th>Ended by</th><th class="r">Result</th><th class="r">R</th></tr></thead>
                <tbody>
                  {live.ended.map((e) => (
                    <tr key={e.id}>
                      <td><b>{e.name}</b> <span class="muted small">{e.action ?? ""}</span></td>
                      <td class="small">{deskName(live, e.desk)}</td>
                      <td class="small">{(e.exit_type ?? dash).toLowerCase()}</td>
                      <td class={`r ${pnlClass(e.pnl)}`}>{signedInr(e.pnl)}{!e.pnl_is_net && <span class="muted small" title="Before costs"> gross</span>}</td>
                      <td class={`r ${pnlClass(e.r)}`}>{signedR(e.r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div class="empty">Nothing has settled today.</div>}
          <p class="muted small" style="margin-top:8px">Strategy-desk results are after estimated costs. The options desk's are before costs; the permanent ledger on the System screen estimates costs for every desk.</p>
        </section>
      </div>
      <aside class="stack">
        <RiskCard live={live} />
        <section class="card">
          <h3>Day loss limit</h3>
          <p class="small muted" style="margin-bottom:6px">New paper entries stop once settled losses reach {live.risk.limits.max_daily_loss.toLocaleString("en-IN")} rupees today.</p>
          <Meter value={Math.max(0, -live.risk.realized_pnl)} max={live.risk.limits.max_daily_loss} />
        </section>
      </aside>
    </div>
  );
}
