import { useState } from "preact/hooks";
import { clockTime, dash, price, shortDate } from "../lib/format";
import { decryptJournal, journalStats, JournalError, type JournalDoc } from "../lib/journalCrypto";
import { fetchText } from "../lib/data";
import { deskName, Pill } from "../components/ui";
import type { AppState } from "../state";

export function JournalScreen({ app }: { app: AppState }) {
  const live = app.snap.live;
  const [pass, setPass] = useState("");
  const [state, setState] = useState<"locked" | "busy" | "open">("locked");
  const [error, setError] = useState("");
  const [doc, setDoc] = useState<JournalDoc | null>(null);
  const published = app.snap.manifest?.files.some((f) => f.path === "journal.bin") ?? app.source.kind === "demo";

  const unlock = async (e: Event) => {
    e.preventDefault();
    if (!pass) return;
    setState("busy");
    setError("");
    try {
      const text = await fetchText(`${app.source.base}journal.bin?t=${Date.now()}`);
      setDoc(await decryptJournal(pass, JSON.parse(text)));
      setState("open");
    } catch (err) {
      setError(err instanceof JournalError ? err.message : (err as Error).message === "not published yet" ? "No journal has been published yet." : "Could not read the journal.");
      setState("locked");
    } finally {
      setPass("");                                  // the passphrase is not kept
    }
  };
  const lock = () => { setDoc(null); setState("locked"); };

  if (state === "open" && doc) {
    const stats = journalStats(doc.entries);
    const rows = [...doc.entries].sort((a, b) => (a.t < b.t ? 1 : -1));
    return (
      <div class="stack">
        <div class="row" style="align-items:center"><h2>Your journal</h2><span class="grow" /><button type="button" class="iconbtn" onClick={lock}>Lock</button></div>
        <div class="grid2">
          <div class="card"><div class="stat-l">Took</div><div class="stat-v">{stats.took}</div></div>
          <div class="card"><div class="stat-l">Skipped</div><div class="stat-v">{stats.skipped}</div></div>
          <div class="card"><div class="stat-l">Real fills logged</div><div class="stat-v">{stats.withFill}</div></div>
          <div class="card"><div class="stat-l">Average slippage</div><div class="stat-v">{stats.meanSlippagePct == null ? dash : `${stats.meanSlippagePct > 0 ? "+" : ""}${stats.meanSlippagePct}%`}</div>
            <div class="muted small">Positive means you paid more than the paper price. The cost model assumes 0.03% a side on shares, 0.01% on index futures and 0.5% on options.</div></div>
        </div>
        <section class="card">
          <h3>Entries</h3>
          {rows.length ? (
            <div class="tablewrap"><table>
              <thead><tr><th>When</th><th>Idea</th><th>Desk</th><th>Decision</th><th class="r">Paper entry</th><th class="r">Your fill</th><th class="r">Slippage</th></tr></thead>
              <tbody>{rows.map((e) => (
                <tr key={e.id}>
                  <td class="small">{shortDate(e.t)} {clockTime(e.t)}</td>
                  <td><b>{e.name}</b> <span class={`side ${e.action === "BUY" ? "buy" : "sell"}`}>{e.action}</span></td>
                  <td class="small">{deskName(live, e.desk)}</td>
                  <td>{e.took ? <Pill tone="ok">took</Pill> : <Pill tone="neutral">skipped</Pill>}</td>
                  <td class="r">{price(e.paper_entry)}</td>
                  <td class="r">{e.fill_price != null ? `${price(e.fill_price)}${e.fill_qty != null ? ` × ${e.fill_qty}` : ""}` : dash}</td>
                  <td class="r">{e.slippage_pct != null ? `${e.slippage_pct > 0 ? "+" : ""}${e.slippage_pct}%` : dash}</td>
                </tr>))}</tbody></table></div>
          ) : <p class="muted">No entries yet.</p>}
        </section>
      </div>
    );
  }

  return (
    <div class="stack" style="max-width:640px">
      <section class="card">
        <h2>Journal</h2>
        <p style="margin:8px 0">Record which ideas you took or skipped, and the price you really got, by tapping <b>Took it</b> or <b>Skip</b> under an alert in Telegram. Comparing your fills with the paper price measures real slippage, the number the cost estimates stand in for.</p>
        <p class="muted small">The journal is encrypted before it is published, so the public data branch holds only ciphertext. Your passphrase is used in this browser to unlock it, once, and is never stored or sent anywhere.</p>
      </section>
      {live && live.journal?.enabled === false && (
        <div class="banner degraded"><strong>The journal is switched off.</strong>Set the JOURNAL_PASSPHRASE secret for the engine to turn on the buttons and start recording.</div>
      )}
      {!published && live?.journal?.enabled !== false && <div class="banner"><strong>No journal has been published yet.</strong>It appears after the first time you tap Took it or Skip on an alert.</div>}
      <form class="card" onSubmit={unlock}>
        <div class="field">
          <label for="journal-pass">Journal passphrase</label>
          <input id="journal-pass" type="password" autocomplete="off" value={pass} onInput={(e) => setPass((e.target as HTMLInputElement).value)} disabled={state === "busy"} />
        </div>
        <div class="row" style="margin-top:10px;align-items:center">
          <button class="btn" type="submit" disabled={state === "busy" || !pass}>{state === "busy" ? "Unlocking…" : "Unlock"}</button>
          {error && <span class="bad small" role="alert">{error}</span>}
        </div>
        {app.source.kind === "demo" && <p class="muted small" style="margin-top:8px">Sample journal. The sample passphrase is <code>correct horse battery staple</code>.</p>}
      </form>
    </div>
  );
}
