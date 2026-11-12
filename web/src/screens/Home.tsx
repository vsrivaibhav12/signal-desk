import { useMemo, useState } from "preact/hooks";
import { IdeaCard } from "../components/IdeaCard";
import { ImpactCard, MarketCard, RiskCard, RiskStrip } from "../components/Rails";
import type { Idea } from "../generated/live";
import { mergeIdeas, type Ranked } from "../lib/ideas";
import { DEFAULT_THRESHOLDS } from "../lib/time";
import type { AppState } from "../state";

export function Home({ app, onOpen, ranked }: { app: AppState; onOpen: (id: string) => void; ranked: Ranked[] }) {
  const { snap, nowMs } = app;
  const live = snap.live;
  const [desk, setDesk] = useState("all");
  const [allWatch, setAllWatch] = useState(false);
  const shown = desk === "all" ? ranked : ranked.filter((r) => r.idea.desk === desk);
  const main = shown.filter((r) => r.eff.grade !== "watch" && r.eff.grade !== "wait");
  const watching = shown.filter((r) => r.eff.grade === "watch" || r.eff.grade === "wait");
  const WATCH_SHOWN = 4;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of ranked) c[r.idea.desk] = (c[r.idea.desk] ?? 0) + 1;
    return c;
  }, [ranked]);
  const actionable = ranked.filter((r) => r.eff.actionable).length;

  if (!live) {
    return <div class="empty">{Object.keys(snap.errors).length ? "Could not load data. Check your connection; this page will keep trying." : "Loading…"}</div>;
  }
  return (
    <>
      <RiskStrip live={live} />
      <div class="cols">
        <section aria-label="Ideas">
          <div class="sec-head">
            <h2>Act now</h2>
            <span class="muted small">{actionable} actionable · {ranked.length} in all · ranked by the engine's quality score, not by expected profit</span>
          </div>
          <div class="chips" role="group" aria-label="Filter by desk">
            <button type="button" class="chip" aria-pressed={desk === "all"} onClick={() => setDesk("all")}>All ({ranked.length})</button>
            {live.desks.filter((d) => counts[d.id]).map((d) => (
              <button key={d.id} type="button" class="chip" aria-pressed={desk === d.id} onClick={() => setDesk(d.id)}>{d.name} ({counts[d.id]})</button>
            ))}
          </div>
          {shown.length ? (
            <>
              {main.length > 0 && (
                <div class="ideas">
                  {main.map((r) => <IdeaCard key={r.idea.id} r={r} app={app} onOpen={onOpen} />)}
                </div>
              )}
              {main.length === 0 && <div class="empty">Nothing has passed every check. The signals below are only being watched.</div>}
              {watching.length > 0 && (
                <>
                  <div class="sec-head" style="margin-top:18px">
                    <h2>Watching</h2>
                    <span class="muted small">{watching.length} signals that did not pass every check. Not ideas.</span>
                  </div>
                  <div class="ideas">
                    {(allWatch ? watching : watching.slice(0, WATCH_SHOWN)).map((r) => <IdeaCard key={r.idea.id} r={r} app={app} onOpen={onOpen} />)}
                  </div>
                  {watching.length > WATCH_SHOWN && (
                    <p style="margin-top:10px"><button type="button" class="iconbtn" onClick={() => setAllWatch(!allWatch)}>{allWatch ? "Show fewer" : `Show all ${watching.length}`}</button></p>
                  )}
                </>
              )}
            </>
          ) : (
            <div class="empty">
              {ranked.length ? "Nothing on this desk right now." : app.trust.state === "closed"
                ? "No ideas from the last session. Swing setups appear here after the evening scan."
                : "Nothing is worth considering right now. That is a normal state: the tool is built to trade rarely."}
            </div>
          )}
        </section>
        <aside class="stack" aria-label="Context">
          <div class="rail-risk"><RiskCard live={live} /></div>
          <MarketCard live={live} />
          <ImpactCard intel={snap.intel} nowMs={nowMs} />
        </aside>
      </div>
    </>
  );
}

export function rankFor(app: AppState): Ranked[] {
  const live = app.snap.live;
  const th = live?.thresholds ?? DEFAULT_THRESHOLDS;
  const swing = (app.snap.swing?.ideas ?? []) as unknown as Idea[];      // swing.json carries ideas in the same shape as live.json
  return mergeIdeas([live?.ideas ?? [], swing], app.trust, app.nowMs, th);
}
