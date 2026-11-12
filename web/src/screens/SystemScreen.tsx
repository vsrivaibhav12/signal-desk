import { useState } from "preact/hooks";
import { allLedgerTrades } from "../lib/data";
import { dash, signedInr, signedR } from "../lib/format";
import { summarizeLedger } from "../lib/ledger";
import { DEFAULT_THRESHOLDS, humanAge } from "../lib/time";
import { AgePill, deskName, Pill, pnlClass } from "../components/ui";
import type { AppState } from "../state";

const STATUS_TONE: Record<string, string> = { fresh: "ok", ok: "ok", complete: "ok", connected: "ok", partial: "degraded", slowed: "degraded", stale: "stale", missing: "stale", invalid: "stale", unavailable: "stale" };

export function SystemScreen({ app }: { app: AppState }) {
  const { snap, trust, nowMs } = app;
  const live = snap.live;
  const th = live?.thresholds ?? DEFAULT_THRESHOLDS;
  const [loading, setLoading] = useState(false);
  const trades = allLedgerTrades(snap);
  const summary = summarizeLedger(trades);
  const loaded = Object.keys(snap.ledger).length > 0;
  const files = snap.manifest?.files ?? [];

  const loadLedger = async () => { setLoading(true); try { await app.ensureLedger(); } finally { setLoading(false); } };

  return (
    <div class="stack">
      <section class="card">
        <div class="card-head"><h2>Data trust</h2><Pill tone={trust.state}>{trust.state}</Pill></div>
        {trust.reasons.length ? <ul class="plain">{trust.reasons.map((r) => <li key={r}>{r}</li>)}</ul> : <p>The engine is watching, prices are recent and its own data checks pass.</p>}
        <dl class="kv" style="margin-top:10px">
          <dt>Source</dt><dd>{app.source.kind === "demo" ? "Sample data (bundled)" : app.source.label}</dd>
          <dt>Last published</dt><dd>{live ? humanAge(trust.publishAge) : dash}</dd>
          <dt>Newest price</dt><dd>{live ? humanAge(trust.priceAge) : dash}</dd>
          <dt>Page last refreshed</dt><dd>{snap.loadedAt ? humanAge(Math.round((Date.now() - snap.loadedAt) / 1000)) : dash}</dd>
        </dl>
        {Object.entries(snap.errors).length > 0 && (
          <p class="reason small" style="margin-top:8px">Could not refresh: {Object.entries(snap.errors).map(([p, why]) => `${p} ${why}`).join("; ")}. The last good copy is shown.</p>
        )}
      </section>

      {live && (
        <section class="card">
          <h2>Engine checks</h2>
          {live.health.items.length ? (
            <div class="tablewrap"><table>
              <thead><tr><th>Input</th><th>Status</th><th>Detail</th></tr></thead>
              <tbody>{live.health.items.map((i) => (
                <tr key={i.name}><td>{i.name}</td><td><Pill tone={STATUS_TONE[i.status] ?? "neutral"}>{i.status}</Pill></td><td class="small">{i.detail}</td></tr>))}</tbody></table></div>
          ) : <p class="muted">No checks published yet.</p>}
          <dl class="kv" style="margin-top:10px">
            {Object.entries(live.brokers as Record<string, { name?: string; status?: string }>).map(([k, b]) => (
              <><dt key={`${k}-t`}>{k === "primary" ? "NSE/BSE feed" : k === "commodity" ? "MCX feed" : k}</dt><dd key={`${k}-d`}>{b.name ?? dash}: {b.status ?? dash}</dd></>))}
          </dl>
        </section>
      )}

      <section class="card">
        <div class="card-head">
          <h2>Settled paper trades</h2>
          <button type="button" class="iconbtn" onClick={loadLedger} disabled={loading || !snap.manifest}>{loading ? "Loading…" : loaded ? "Reload" : "Load the last 3 months"}</button>
        </div>
        {loaded ? (
          summary.length ? (
            <div class="tablewrap"><table>
              <thead><tr><th>Desk</th><th class="r">Trades</th><th class="r">Won</th><th class="r">Lost</th><th class="r">Average R</th><th class="r">Net (rupees)</th></tr></thead>
              <tbody>{summary.map((s) => (
                <tr key={s.desk}><td>{deskName(live, s.desk)}{s.unresolved > 0 && <div class="reason small">{s.unresolved} unresolved</div>}</td><td class="r">{s.trades}</td><td class="r">{s.wins}</td><td class="r">{s.losses}</td>
                  <td class={`r ${pnlClass(s.avgR)}`}>{signedR(s.avgR)}</td><td class={`r ${pnlClass(s.netRupees)}`}>{s.netRupees == null ? "per share" : signedInr(s.netRupees)}</td></tr>))}</tbody></table></div>
          ) : <p class="muted">The ledger is empty so far.</p>
        ) : <p class="muted">The ledger is the permanent record. It is loaded only when you ask.</p>}
        <p class="muted small" style="margin-top:8px">After estimated costs. The research found no strategy with a reliable edge after costs, so a short record here says little either way. The swing desk's trades are measured per share, in R.</p>
      </section>

      <section class="card">
        <h2>Published files</h2>
        {files.length ? (
          <div class="tablewrap"><table>
            <thead><tr><th>File</th><th class="r">Size</th><th>Changed</th></tr></thead>
            <tbody>{files.map((f) => (
              <tr key={f.path}><td class="small"><code>{f.path}</code></td><td class="r small">{f.bytes > 1024 ? `${Math.round(f.bytes / 1024)} KB` : `${f.bytes} B`}</td><td><AgePill iso={f.updated_at} nowMs={nowMs} th={th} /></td></tr>
            ))}</tbody></table></div>
        ) : <p class="muted">The manifest has not been published yet.</p>}
      </section>

      <section class="card">
        <h2>About</h2>
        <p style="margin-top:6px">Signal Desk reads market data and paper-trades. It places no orders and is not a recommendation. {live?.evidence ? (live.evidence as { system?: { headline?: string } }).system?.headline : ""}</p>
        <p class="muted small" style="margin-top:6px">Full research notes: docs/STRATEGY_RESEARCH.md in the repository. The previous dashboard, with the long research tables, is kept at <a href="./legacy/">legacy/</a> until it is retired.</p>
      </section>
    </div>
  );
}
