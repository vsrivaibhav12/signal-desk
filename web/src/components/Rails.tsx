import type { Live } from "../generated/live";
import { clockTime, dash, inr, price, safeHttpUrl, signedInr } from "../lib/format";
import { ageSeconds, humanAge } from "../lib/time";
import type { Intel, Regime, Tile } from "../lib/types";
import { Meter, pnlClass, Pill } from "./ui";

export function RiskCard({ live }: { live: Live }) {
  const { risk } = live;
  const lossUsed = Math.max(0, -risk.realized_pnl);
  return (
    <section class="card" aria-label="Risk budget">
      <div class="card-head"><h3>Risk budget</h3><span class="muted small">paper, shared by every desk</span></div>
      <div class="stat-l">Positions</div>
      <div class="stat-v">{risk.open_positions} <span class="muted small">of {risk.limits.max_open_positions}</span></div>
      <Meter value={risk.open_positions} max={risk.limits.max_open_positions} />
      <div class="stat-l" style="margin-top:10px">Open risk</div>
      <div class="stat-v">{inr(risk.open_risk)} <span class="muted small">of {inr(risk.limits.max_open_risk)}</span></div>
      <Meter value={risk.open_risk} max={risk.limits.max_open_risk} />
      <div class="stat-l" style="margin-top:10px">Day P&amp;L</div>
      <div class={`stat-v ${pnlClass(risk.day_pnl)}`}>{signedInr(risk.day_pnl)}</div>
      <dl class="kv" style="margin-top:6px">
        <dt>Settled</dt><dd class={pnlClass(risk.realized_pnl)}>{signedInr(risk.realized_pnl)}</dd>
        <dt>Open</dt><dd class={pnlClass(risk.open_pnl)}>{signedInr(risk.open_pnl)}</dd>
        <dt>Loss limit used</dt><dd>{inr(lossUsed)} of {inr(risk.limits.max_daily_loss)}</dd>
      </dl>
    </section>
  );
}

export function RiskStrip({ live }: { live: Live }) {
  const { risk } = live;
  return (
    <div class="riskstrip" aria-label="Risk summary">
      <div class="card"><div class="stat-l">Positions</div><div class="stat-v">{risk.open_positions}/{risk.limits.max_open_positions}</div><Meter value={risk.open_positions} max={risk.limits.max_open_positions} /></div>
      <div class="card"><div class="stat-l">Open risk</div><div class="stat-v">{inr(risk.open_risk)}</div><Meter value={risk.open_risk} max={risk.limits.max_open_risk} /></div>
      <div class="card"><div class="stat-l">Day P&amp;L</div><div class={`stat-v ${pnlClass(risk.day_pnl)}`}>{signedInr(risk.day_pnl)}</div></div>
    </div>
  );
}

const asTiles = (live: Live): Tile[] => (live.market.tiles as Tile[]).filter((t) => t.price != null && (t.book === "Index options" || t.book === "Commodity options"));

export function MarketCard({ live }: { live: Live }) {
  const tiles = asTiles(live).slice(0, 6);
  const regime = live.market.regime as Regime | null;
  const nifty = regime?.markets?.find((m) => m.name === "Nifty 50");
  const leaders = live.market.leaders as { breadth?: { above200: number; above50: number }; asof?: string; n_leaders?: number } | null;
  return (
    <section class="card" aria-label="Market read">
      <div class="card-head"><h3>Market read</h3><span class="muted small">{live.market.watchlist_count} on the watchlist</span></div>
      {nifty && (
        <p style="margin-bottom:8px">
          <b>Nifty 50</b> {price(nifty.price)} <span class={pnlClass(nifty.chg)}>{nifty.chg != null ? `${nifty.chg > 0 ? "+" : ""}${nifty.chg}%` : ""}</span>
          <span class="muted small"> · {nifty.vwap} VWAP · {nifty.label.toLowerCase()} · trend {nifty.trend}</span>
        </p>
      )}
      {tiles.length > 0 && (
        <table aria-label="Index and commodity prices">
          <tbody>
            {tiles.map((t) => {
              const chg = t.price != null && t.prev_close ? ((t.price - t.prev_close) / t.prev_close) * 100 : null;
              return (
                <tr key={t.id ?? t.name}>
                  <td>{t.name}</td>
                  <td class="r">{price(t.price)}</td>
                  <td class={`r ${pnlClass(chg)}`}>{chg != null ? `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%` : dash}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {leaders?.breadth && (
        <p class="muted small" style="margin-top:8px">
          Breadth ({leaders.asof}): {Math.round(leaders.breadth.above200 * 100)}% of liquid shares above their 200-day average, {Math.round(leaders.breadth.above50 * 100)}% above their 50-day.
        </p>
      )}
      {!nifty && tiles.length === 0 && <p class="muted">No market prices have been published yet.</p>}
    </section>
  );
}

export function ImpactList({ intel, limit, nowMs }: { intel: Intel | undefined; limit: number; nowMs: number }) {
  const events = intel?.events ?? [];
  if (!events.length) return <p class="muted">{intel ? "No relevant headlines are being tracked right now." : "The news service has not published yet."}</p>;
  return (
    <div>
      {events.slice(0, limit).map((e, i) => {
        const link = safeHttpUrl(e.link);
        const title = e.title ?? "Untitled";
        return (
          <article class="impact" key={e.id ?? i}>
            <div class="idea-top">
              <Pill tone="neutral">{e.category ?? "Market news"}</Pill>
              <span class="small muted">relevance {Math.round(e.impact_score ?? 0)}/100</span>
              <span class="grow" />
              <span class="small muted">{clockTime(e.published_at)} · {humanAge(ageSeconds(nowMs, e.published_at))}</span>
            </div>
            <h3 style="margin:6px 0 4px">{link ? <a href={link} target="_blank" rel="noopener noreferrer">{title}</a> : title}</h3>
            {e.summary && <p class="small">{e.summary}</p>}
            {(e.impacts ?? []).slice(0, 4).map((p, k) => (
              <p class="small" key={k} style="margin-top:3px">
                <b>{p.label ?? p.asset}</b> <span class={`bias-${p.bias ?? "mixed"}`}>{(p.bias ?? "unclear").toUpperCase()}</span>
                {p.why && <span class="muted"> · {p.why}</span>}
              </p>
            ))}
          </article>
        );
      })}
      <p class="muted small" style="margin-top:10px">{intel?.disclaimer ?? "Relevance ranks headlines. It is not the probability of a price move, and no headline becomes a trade on its own."}</p>
    </div>
  );
}

export function ImpactCard({ intel, nowMs }: { intel: Intel | undefined; nowMs: number }) {
  const last = intel?.status?.last_scan;
  const age = ageSeconds(nowMs, last);
  return (
    <section class="card" aria-label="Impact radar">
      <div class="card-head"><h3>Impact radar</h3><span class="muted small">{last ? `scanned ${humanAge(age)}` : "not scanned yet"}</span></div>
      <ImpactList intel={intel} limit={2} nowMs={nowMs} />
    </section>
  );
}
