import { dash, price } from "../lib/format";
import type { LeadersPublic, Mover, Regime, ResultEvent, ResultsPublic, Tile } from "../lib/types";
import { ImpactList } from "../components/Rails";
import { Pill, pnlClass } from "../components/ui";
import type { AppState } from "../state";

const names = (ev: ResultEvent[] | undefined) => (ev?.length ? ev.map((e) => e.symbol).join(", ") : "none");

export function Market({ app }: { app: AppState }) {
  const live = app.snap.live;
  if (!live) return <div class="empty">Loading…</div>;
  const tiles = (live.market.tiles as Tile[]).filter((t) => t.price != null);
  const regime = live.market.regime as Regime | null;
  const movers = live.market.movers as Mover[];
  const results = live.market.results as ResultsPublic | null;
  const leaders = live.market.leaders as LeadersPublic | null;
  return (
    <div class="stack">
      <section class="card">
        <div class="card-head"><h2>Indices and commodities</h2><span class="muted small">{tiles.length} with prices</span></div>
        {tiles.length ? (
          <div class="grid2">
            {tiles.map((t) => {
              const chg = t.price != null && t.prev_close ? ((t.price - t.prev_close) / t.prev_close) * 100 : null;
              return (
                <div class="tile" key={t.id ?? t.name}>
                  <span class="t-name">{t.name} {t.in_trade && <Pill tone="demo">in a trade</Pill>}</span>
                  <span class="t-price">{price(t.price)} <span class={`small ${pnlClass(chg)}`}>{chg != null ? `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%` : ""}</span></span>
                  <span class="muted small">{t.vwap != null ? `VWAP ${price(t.vwap)}` : ""}{t.note ? ` · ${t.note}` : ""}</span>
                </div>
              );
            })}
          </div>
        ) : <p class="muted">No prices have been published yet.</p>}
      </section>

      <div class="grid2">
        <section class="card">
          <h2>Market conditions</h2>
          {regime?.markets?.length ? (
            <table style="margin-top:8px"><thead><tr><th>Index</th><th>Trend</th><th>Strength</th><th>Vs VWAP</th></tr></thead>
              <tbody>{regime.markets.map((m) => <tr key={m.name}><td>{m.name}</td><td>{m.trend}</td><td>{m.label}{m.adx != null ? ` (ADX ${m.adx})` : ""}</td><td>{m.vwap}</td></tr>)}</tbody></table>
          ) : <p class="muted">Not enough candles yet.</p>}
          {regime?.breadth && (
            <p class="small muted" style="margin-top:8px">
              Of {regime.breadth.stocks} shares scanned: {regime.breadth.trending ?? dash}% trending, {regime.breadth.above_vwap ?? dash}% above VWAP, {regime.breadth.up_on_day ?? dash}% up on the day.
            </p>
          )}
        </section>
        <section class="card">
          <h2>Results</h2>
          {results ? (
            <dl class="kv" style="margin-top:8px">
              <dt>Just out</dt><dd>{names(results.last)}</dd>
              <dt>Today</dt><dd>{names(results.today)}</dd>
              <dt>Next session</dt><dd>{names(results.next)}</dd>
            </dl>
          ) : <p class="muted">The results calendar has not been read yet.</p>}
          {results && !results.ok && <p class="reason small">The results calendar could not be read; this list may be incomplete.</p>}
          {leaders?.breadth && (
            <p class="small muted" style="margin-top:10px">
              Relative strength ({leaders.asof}): {leaders.n_leaders} leaders among {leaders.universe} liquid shares. {Math.round(leaders.breadth.above200 * 100)}% are above their 200-day average{leaders.breadth.bear_phase ? ", which the playbook calls a bear phase" : ""}.
            </p>
          )}
        </section>
      </div>

      <section class="card">
        <div class="card-head"><h2>Movers</h2><span class="muted small">"Genuine" = big move, heavy volume, above/below VWAP and holding</span></div>
        {movers.length ? (
          <div class="tablewrap">
            <table>
              <thead><tr><th>Share</th><th class="r">Price</th><th class="r">Move</th><th class="r">Volume</th><th>Holding</th><th /></tr></thead>
              <tbody>
                {movers.map((m) => (
                  <tr key={m.name}>
                    <td><b>{m.name}</b></td>
                    <td class="r">{price(m.price)}</td>
                    <td class={`r ${m.dir > 0 ? "good" : "bad"}`}>{m.move > 0 ? "+" : ""}{m.move}%</td>
                    <td class="r">{m.vol != null ? `${m.vol}x` : dash}</td>
                    <td>{Math.round(m.held * 100)}% of range</td>
                    <td>{m.genuine && <Pill tone="ok">genuine</Pill>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p class="muted">No share has moved enough to list.</p>}
      </section>

      <section class="card">
        <div class="card-head"><h2>Impact radar</h2><span class="muted small">headline → path → instruments → confirmation</span></div>
        <ImpactList intel={app.snap.intel} limit={12} nowMs={app.nowMs} />
      </section>
    </div>
  );
}
