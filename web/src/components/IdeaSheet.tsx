import { useEffect, useRef, useState } from "preact/hooks";
import type { Live } from "../generated/live";
import { clockTime, dash, inr, price } from "../lib/format";
import { checkSummary, orderTicket, type Ranked } from "../lib/ideas";
import { ageSeconds, humanAge } from "../lib/time";
import { deskName, GradeBadge, markOf } from "./ui";

const FACTS: Array<[string, string, (v: unknown) => string]> = [
  ["in_play", "Why it is on the list", String],
  ["rs", "Relative strength", (v) => String(Math.round(Number(v)))],
  ["volume_x", "Volume against normal", (v) => `${v}x`],
  ["regime", "Setup", String],
  ["pivot", "40-day high (pivot)", (v) => price(Number(v))],
  ["volx", "Breakout volume", (v) => `${Number(v).toFixed(1)}x normal`],
  ["spread_pct", "Bid-ask spread", (v) => `${v}%`],
  ["oi", "Open interest", (v) => String(Math.round(Number(v)))],
  ["expiry", "Expiry", String],
  ["exit_by", "Exit by", String],
];

export function IdeaSheet({ r, live, nowMs, onClose }: { r: Ranked; live: Live | undefined; nowMs: number; onClose: () => void }) {
  const { idea, eff } = r;
  const ref = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState<"idle" | "ok" | "failed">("idle");
  const ticket = orderTicket(idea, eff, nowMs);
  const summary = checkSummary(idea.checks);
  const ctx = idea.context as Record<string, unknown>;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; previous?.focus?.(); };
  }, [onClose]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(ticket);
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <div class="scrim" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="sheet" role="dialog" aria-modal="true" aria-label={`${idea.name} details`} tabIndex={-1} ref={ref}>
        <div class="sheet-head">
          <div>
            <div class="idea-top">
              <h3 style="font-size:1.2rem">{idea.name}</h3>
              <span class={`side ${idea.action === "BUY" ? "buy" : "sell"}`}>{idea.action}</span>
              <GradeBadge eff={eff} />
            </div>
            <div class="muted small">{deskName(live, idea.desk)} · signalled {clockTime(idea.signaled_at)} ({humanAge(ageSeconds(nowMs, idea.signaled_at))}){idea.contract ? ` · ${idea.contract}` : ""}</div>
          </div>
          <button type="button" class="iconbtn" onClick={onClose} aria-label="Close details">Close</button>
        </div>

        {eff.reasons.length > 0 && eff.grade !== "setup" && (
          <div class={`banner ${eff.grade === "expired" ? "stale" : "degraded"}`}>
            <strong>{eff.grade === "take" ? "Note" : `Published as ${eff.stated.toUpperCase()}, shown as ${eff.grade.toUpperCase()}`}</strong>
            <ul>{eff.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
          </div>
        )}

        <div class="nums" style="margin-top:0">
          <div><span class="l">{idea.desk === "swing" ? "Reference close" : "Entry"}</span><span class="v">{price(idea.entry)}</span></div>
          <div><span class="l">Stop</span><span class="v">{price(idea.stop)}</span></div>
          <div><span class="l">Target</span><span class="v">{idea.target != null ? price(idea.target) : dash}</span></div>
          <div><span class="l">{idea.desk === "swing" ? "Initial risk" : "Risk"}</span><span class="v">{idea.risk != null ? inr(idea.risk) : idea.desk === "swing" ? "8% of entry" : dash}</span></div>
        </div>

        <h4>Checks behind the grade{summary.text ? ` · ${summary.text}` : ""}</h4>
        {idea.checks.length ? (
          <ul class="checks">
            {idea.checks.map((c) => (
              <li key={c.id} class={c.state}>
                <span class="mark" aria-hidden="true">{markOf(c.state)}</span>
                <span>{c.label}{c.state === "unknown" ? " (could not be verified, which counts as not passing)" : c.state === "na" ? " (does not apply)" : c.state === "fail" ? " (failed)" : ""}</span>
              </li>
            ))}
          </ul>
        ) : <p class="muted small">This desk's filters are all-or-nothing: an idea only exists once every one of them has passed.</p>}

        {idea.blockers.length > 0 && (<><h4>What is holding it back</h4><ul class="plain">{idea.blockers.map((b) => <li key={b}>{b}</li>)}</ul></>)}
        {idea.why.length > 0 && (<><h4>Why it was flagged</h4><ul class="plain">{idea.why.map((w) => <li key={w}>{w}</li>)}</ul></>)}

        {FACTS.some(([k]) => ctx[k] != null && ctx[k] !== "") && (
          <>
            <h4>Details</h4>
            <dl class="kv">
              {FACTS.filter(([k]) => ctx[k] != null && ctx[k] !== "").map(([k, label, fmt]) => (<><dt key={`${k}-t`}>{label}</dt><dd key={`${k}-d`}>{fmt(ctx[k])}</dd></>))}
            </dl>
          </>
        )}

        <h4>What the research says about this kind of idea</h4>
        <p style="font-size:.92rem">{idea.evidence?.headline ?? "No research record exists for this kind of idea."}</p>
        {idea.evidence && <p class="muted small">From {idea.evidence.source}, as of {idea.evidence.asof}. A TAKE means the idea passed this tool's own checks. It does not mean it is expected to make money.</p>}

        <h4>For your broker app</h4>
        <div class="ticket" aria-label="Order notes">{ticket}</div>
        <div class="row" style="align-items:center">
          <button type="button" class="btn" onClick={copy}>Copy notes</button>
          <span class="muted small" role="status">{copied === "ok" ? "Copied." : copied === "failed" ? "Could not copy. Select the text above and copy it by hand." : "Signal Desk never places an order; this is for you to read off."}</span>
        </div>
      </div>
    </div>
  );
}
