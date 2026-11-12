import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import type { Idea } from "../generated/live";
import type { Swing } from "../generated/swing";
import { ChartBlock } from "../components/ChartBlock";
import { PlanList } from "../components/PlanList";
import { dash, inr, price, shortDate, signedR } from "../lib/format";
import { swingSize } from "../lib/sizing";
import { Pill, pnlClass } from "../components/ui";
import { useLocalPref, type AppState } from "../state";

function Sizer({ entry, stopPct }: { entry: number; stopPct: number }) {
  const [capital, setCapital] = useLocalPref<number>("sd_swing_capital", 500000);
  const [riskPct, setRiskPct] = useLocalPref<number>("sd_swing_risk", 0.5);
  const size = swingSize({ capital, riskPct, entry, stopPct });
  return (
    <div class="row" style="margin-top:10px">
      <div class="field"><label for={`cap-${entry}`}>Your capital (₹)</label>
        <input id={`cap-${entry}`} type="number" min="0" step="1000" value={capital} onInput={(e) => setCapital(Number((e.target as HTMLInputElement).value))} /></div>
      <div class="field"><label for={`risk-${entry}`}>Risk per trade (%)</label>
        <input id={`risk-${entry}`} type="number" min="0" max="100" step="0.1" value={riskPct} onInput={(e) => setRiskPct(Number((e.target as HTMLInputElement).value))} /></div>
      <div class="field"><label>Shares at the reference close</label>
        <div style="padding:8px 0;font-weight:700">{size.qty} <span class="muted small">{size.qty ? `· ${inr(size.value)} invested · ${inr(size.risk)} at risk if stopped` : "· too small for this risk"}</span></div></div>
    </div>
  );
}

const chartRef = (key: string) => ({ file: "swing.json", in: "charts", key });

/** A table row with a button that opens its chart in a row underneath. `columns` is the number of columns including the button's. */
function ChartRow({ app, name, k, columns, children }: { app: AppState; name: string; k: string; columns: number; children: ComponentChildren }) {
  const [open, setOpen] = useState(false);
  const has = !!(app.snap.swing as unknown as { charts?: Record<string, unknown> } | undefined)?.charts?.[k];
  return (
    <>
      <tr>
        {children}
        <td>{has ? <button type="button" class="iconbtn" aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} the chart for ${name}`} onClick={() => setOpen(!open)}>{open ? "Hide" : "Chart"}</button> : <span class="muted small">none</span>}</td>
      </tr>
      {open && has && <tr class="chartrow"><td colSpan={columns}><ChartBlock app={app} chartRef={chartRef(k)} name={name} /></td></tr>}
    </>
  );
}

export function SwingScreen({ app }: { app: AppState }) {
  const sw: Swing | undefined = app.snap.swing;
  if (!sw) return <div class="empty">{app.snap.errors["swing.json"] ? "The swing scan has not published yet. It runs each weekday evening." : "Loading…"}</div>;
  const above = sw.regime.nifty_above_200;
  return (
    <div class="stack">
      <div class="banner">
        <strong>One unproven rule, shown honestly.</strong>
        {sw.note}
      </div>

      <section class="card">
        <div class="card-head">
          <h2>Setups at the {shortDate(sw.asof)} close</h2>
          <Pill tone={above === true ? "ok" : above === false ? "stale" : "unknown"}>
            {above === true ? "Nifty above its 200-day average" : above === false ? "Nifty below its 200-day average: no new setups" : "Market filter unknown: no new setups"}
          </Pill>
        </div>
        {sw.triggered.length ? (
          <div class="stack">
            {sw.triggered.map((t) => (
              <article key={t.symbol} class="card" style="background:var(--surface-2)">
                <div class="idea-top"><span class="idea-name">{t.symbol}</span><span class="badge setup">SETUP</span><span class="muted small">relative strength {Math.round(t.rs)} · {t.volx.toFixed(1)}x normal volume</span></div>
                <div class="nums">
                  <div><span class="l">Close</span><span class="v">{price(t.close)}</span></div>
                  <div><span class="l">Broke above</span><span class="v">{price(t.pivot)}</span></div>
                  <div><span class="l">Stop (about)</span><span class="v">{price(t.stop_hint)}</span></div>
                  <div><span class="l">20-day average</span><span class="v">{price(t.sma20)}</span></div>
                </div>
                <ChartBlock app={app} chartRef={chartRef(`${t.symbol}:${sw.asof}`)} name={t.symbol} />
                <PlanList plan={(sw.ideas as unknown as Idea[]).find((i) => i.name === t.symbol)?.plan} />
                <Sizer entry={t.close} stopPct={t.risk_pct} />
              </article>
            ))}
          </div>
        ) : <div class="empty">No share met every condition at this close. That is the usual case.</div>}
      </section>

      <section class="card">
        <div class="card-head"><h2>Paper book</h2><span class="muted small">followed exactly as the research simulated it</span></div>
        {sw.positions.length ? (
          <div class="tablewrap"><table>
            <thead><tr><th>Share</th><th>Status</th><th class="r">Entry</th><th class="r">Stop</th><th class="r">Last close</th><th class="r">Progress</th><th>Chart</th></tr></thead>
            <tbody>{sw.positions.map((p) => (
              <ChartRow key={p.id} app={app} name={p.symbol} k={p.id} columns={7}>
                <td><b>{p.symbol}</b><div class="muted small">signal {shortDate(p.signal_date)}</div></td>
                <td>{p.status === "PENDING" ? "Enters at the next open" : p.status === "EXIT_PENDING" ? "Leaves at the next open" : p.breakeven ? "Open, stop at entry" : "Open"}</td>
                <td class="r">{price(p.entry)}</td><td class="r">{price(p.stop)}</td><td class="r">{price(p.last_close)}</td>
                <td class={`r ${pnlClass(p.open_r)}`}>{signedR(p.open_r)}</td>
              </ChartRow>))}</tbody></table></div>
        ) : <p class="muted">The book is empty.</p>}
        {sw.recently_closed.length > 0 && (
          <>
            <h3 style="margin:14px 0 6px">Recently closed</h3>
            <div class="tablewrap"><table>
              <thead><tr><th>Share</th><th>Held</th><th>Ended by</th><th class="r">Result (after costs)</th></tr></thead>
              <tbody>{sw.recently_closed.map((c) => (
                <tr key={c.id}><td><b>{c.symbol}</b></td><td class="small">{shortDate(c.entry_date)} to {shortDate(c.exit_date)}</td><td class="small">{c.reason}</td><td class={`r ${pnlClass(c.r)}`}>{signedR(c.r)}</td></tr>))}</tbody></table></div>
          </>
        )}
      </section>

      <section class="card">
        <div class="card-head"><h2>Near their pivot</h2><span class="muted small">leaders with a tight base, within {(sw.rules as Record<string, number>).near_pivot_pct}% below the break level</span></div>
        {sw.setups.length ? (
          <div class="tablewrap"><table>
            <thead><tr><th>Share</th><th class="r">Close</th><th class="r">Break level</th><th class="r">To go</th><th class="r">Base depth</th><th class="r">Strength</th><th>Chart</th></tr></thead>
            <tbody>{sw.setups.map((s) => (
              <ChartRow key={s.symbol} app={app} name={s.symbol} k={`${s.symbol}:setup`} columns={7}>
                <td><b>{s.symbol}</b></td><td class="r">{price(s.close)}</td><td class="r">{price(s.pivot)}</td><td class="r">{s.pct_to_pivot}%</td><td class="r">{s.base_depth_pct}%</td><td class="r">{Math.round(s.rs)}</td>
              </ChartRow>))}</tbody></table></div>
        ) : <p class="muted">None at this close.</p>}
        <p class="muted small" style="margin-top:8px">A watch list, not ideas: nothing here has broken out yet.</p>
      </section>

      {sw.gap_watch.length > 0 && (
        <section class="card">
          <div class="card-head"><h2>Gap-ups under observation</h2><Pill tone="neutral">hypothesis</Pill></div>
          <div class="tablewrap"><table>
            <thead><tr><th>Share</th><th class="r">Gap</th><th class="r">Volume</th><th>Results</th><th class="r">Gap-day low</th></tr></thead>
            <tbody>{sw.gap_watch.map((g) => (
              <tr key={g.symbol}><td><b>{g.symbol}</b></td><td class="r">+{g.gap_pct}%</td><td class="r">{g.volx}x</td><td>{g.results ? "Results announced" : "No results"}</td><td class="r">{price(g.stop_hint)}</td></tr>))}</tbody></table></div>
          <p class="muted small" style="margin-top:8px">The one lead from the market-research test: 28 trades, chosen after the first result was seen, so it proves nothing. These are listed only to build evidence; they are not paper-traded and not ideas.</p>
        </section>
      )}

      <p class="muted small">{sw.evidence?.headline ?? dash} Rules are fixed in engine/daily.py and have not been tuned.</p>
    </div>
  );
}
