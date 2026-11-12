import type { ComponentChildren } from "preact";
import type { Check, Live } from "../generated/live";
import type { Effective } from "../lib/ideas";
import { dash, percent } from "../lib/format";
import { ageSeconds, freshness, humanAge, type Thresholds } from "../lib/time";
import type { Trust } from "../lib/trust";

const GRADE_LABEL: Record<Effective["grade"], string> = { take: "TAKE", setup: "SETUP", watch: "WATCH", wait: "WAIT", expired: "EXPIRED" };

export function GradeBadge({ eff }: { eff: Effective }) {
  const demoted = eff.grade !== eff.stated && eff.grade !== "setup";
  return (
    <span class={`badge ${eff.grade}`} title={demoted ? `Published as ${eff.stated.toUpperCase()}; shown as ${GRADE_LABEL[eff.grade]} because ${eff.reasons[0] ?? "of the data"}` : undefined}>
      {GRADE_LABEL[eff.grade]}
    </span>
  );
}

export function Pill({ tone, children, title }: { tone: string; children: ComponentChildren; title?: string }) {
  return <span class={`pill ${tone}`} title={title}>{children}</span>;
}

export function AgePill({ iso, nowMs, th, label }: { iso: string | null | undefined; nowMs: number; th: Thresholds; label?: string }) {
  const age = ageSeconds(nowMs, iso);
  const f = freshness(age, th);
  return <Pill tone={f === "fresh" ? "fresh" : f === "amber" ? "amber" : f === "stale" ? "stale" : "unknown"}>{label ? `${label} ` : ""}{humanAge(age)}</Pill>;
}

const MARK: Record<Check["state"], string> = { pass: "✓", fail: "✗", unknown: "?", na: "–" };
export const markOf = (s: Check["state"]) => MARK[s];

export function CheckDots({ checks }: { checks: Check[] }) {
  if (!checks.length) return null;
  return (
    <span class="dots" role="img" aria-label={checks.map((c) => `${c.label}: ${c.state}`).join(", ")}>
      {checks.map((c) => <i key={c.id} class={c.state} title={`${c.label}: ${c.state === "unknown" ? "could not be verified" : c.state}`} />)}
    </span>
  );
}

export function Meter({ value, max }: { value: number; max: number }) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const tone = ratio >= 1 ? "full" : ratio >= 0.7 ? "hot" : "";
  return (
    <div class={`meter ${tone}`} role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.round(value)}>
      <i style={{ width: `${Math.round(ratio * 100)}%` }} />
    </div>
  );
}

export function deskName(live: Live | undefined, id: string): string {
  return live?.desks.find((d) => d.id === id)?.name ?? id;
}

export function TrustPill({ trust, demo }: { trust: Trust; demo: boolean }) {
  const text: Record<Trust["state"], string> = { ok: "Live", degraded: "Check data", stale: "Stale", closed: "Session closed", offline: "No data" };
  return (
    <span style="display:inline-flex;gap:6px;flex-wrap:wrap">
      {demo && <Pill tone="demo">Sample data</Pill>}
      <Pill tone={trust.state} title={trust.reasons.join(" ")}><span class="dot" />{text[trust.state]}</Pill>
    </span>
  );
}

export function Banner({ trust, demo, simulatedStale }: { trust: Trust; demo: boolean; simulatedStale: boolean }) {
  return (
    <div aria-live="polite">
      {demo && (
        <div class="banner demo">
          <strong>You are looking at sample data.</strong>
          Everything here is made up to show how the cockpit works.{simulatedStale ? " The data is being shown as if the engine had stopped, to show how stale numbers look." : " Add ?demo&stale to the address to see how stale data looks."}
        </div>
      )}
      {trust.state !== "ok" && (
        <div class={`banner ${trust.state}`}>
          <strong>
            {trust.state === "stale" || trust.state === "offline" ? "Do not act on what you see here." : trust.state === "closed" ? "The market session is closed." : "Some data needs attention."}
          </strong>
          {trust.state === "stale" || trust.state === "offline" ? "TAKE badges are removed while the data is stale." : trust.state === "degraded" ? "TAKE badges are shown as WATCH until it recovers." : ""}
          <ul>{trust.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

export function pnlClass(n: number | null | undefined): string {
  return n == null ? "" : n > 0 ? "good" : n < 0 ? "bad" : "";
}

export const pct = (n: number | null | undefined, d = 0) => (n == null ? dash : percent(n, d));
