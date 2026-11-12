// What an idea means right now. The engine states a grade when it publishes; the page may only ever make it MORE cautious.
import type { Check, Idea } from "../generated/live";
import { clockTime, inr, price } from "./format";
import { ageSeconds, DEFAULT_THRESHOLDS, humanAge, type Thresholds } from "./time";
import type { Trust } from "./trust";

export type EffectiveGrade = "take" | "setup" | "watch" | "wait" | "expired";

export interface Effective {
  grade: EffectiveGrade;
  stated: Idea["grade"];
  ageSeconds: number | null;
  /** Plain-language reasons the page changed or qualified the stated grade. */
  reasons: string[];
  /** True only when it is sensible to consider acting right now. */
  actionable: boolean;
}

const SWING_EXPIRY_S = 4 * 86400;

export function effectiveIdea(idea: Idea, trust: Trust, nowMs: number, th: Thresholds = DEFAULT_THRESHOLDS): Effective {
  const age = ageSeconds(nowMs, idea.signaled_at);
  const base = { stated: idea.grade, ageSeconds: age };

  if (idea.desk === "swing") {
    if (age !== null && age > SWING_EXPIRY_S) {
      return { ...base, grade: "expired", reasons: ["This setup is from an old close."], actionable: false };
    }
    return { ...base, grade: idea.grade, reasons: [], actionable: idea.grade === "setup" };
  }
  if (idea.grade !== "take") return { ...base, grade: idea.grade, reasons: [], actionable: false };

  // From here it is a TAKE as published. The page can only take that away, never add to it.
  if (trust.state === "offline" || trust.state === "stale") {
    return { ...base, grade: "expired", reasons: ["The data is stale, so this is not actionable.", ...trust.reasons], actionable: false };
  }
  if (trust.state === "degraded") {
    return { ...base, grade: "watch", reasons: ["Shown as WATCH because the data is not fully trustworthy right now.", ...trust.reasons], actionable: false };
  }
  if (trust.state === "closed") {
    return { ...base, grade: "take", reasons: ["The session is closed. This is the last signal, not a live one."], actionable: false };
  }
  if (age === null) {
    return { ...base, grade: "expired", reasons: ["The signal has no usable time, so its age is unknown."], actionable: false };
  }
  if (age > th.idea_expire_s) {
    return { ...base, grade: "expired", reasons: [`Signalled ${humanAge(age)}. The setup has probably moved on.`], actionable: false };
  }
  return { ...base, grade: "take", reasons: [], actionable: true };
}

const ORDER: Record<EffectiveGrade, number> = { take: 0, setup: 1, watch: 2, wait: 3, expired: 4 };

export interface Ranked {
  idea: Idea;
  eff: Effective;
}

/** Actionable grades first, then by the engine's quality score, then newest first. Expired ideas sink to the bottom. */
export function rankIdeas(items: Ranked[]): Ranked[] {
  const time = (r: Ranked) => {
    const t = r.idea.signaled_at ? Date.parse(r.idea.signaled_at) : NaN;
    return Number.isNaN(t) ? 0 : t;
  };
  return [...items].sort((a, b) => ORDER[a.eff.grade] - ORDER[b.eff.grade] || b.idea.quality - a.idea.quality || time(b) - time(a));
}

/** Both lists merged by id (the first one wins), each idea paired with what it means now. */
export function mergeIdeas(lists: Idea[][], trust: Trust, nowMs: number, th: Thresholds = DEFAULT_THRESHOLDS): Ranked[] {
  const seen = new Set<string>();
  const out: Ranked[] = [];
  for (const list of lists) {
    for (const idea of list) {
      if (seen.has(idea.id)) continue;
      seen.add(idea.id);
      out.push({ idea, eff: effectiveIdea(idea, trust, nowMs, th) });
    }
  }
  return rankIdeas(out);
}

export interface CheckSummary {
  pass: number;
  fail: number;
  unknown: number;
  applicable: number;
  text: string;
}

export function checkSummary(checks: Check[]): CheckSummary {
  const count = (s: Check["state"]) => checks.filter((c) => c.state === s).length;
  const pass = count("pass"), fail = count("fail"), unknown = count("unknown");
  const applicable = pass + fail + unknown;
  if (applicable === 0) return { pass, fail, unknown, applicable, text: "" };
  const parts = [`${pass} of ${applicable} checks passed`];
  if (fail) parts.push(`${fail} failed`);
  if (unknown) parts.push(`${unknown} not verified`);
  return { pass, fail, unknown, applicable, text: parts.join(", ") };
}

/** Plain text to read off while placing the order yourself in your broker app. The tool places nothing. */
export function orderTicket(idea: Idea, eff: Effective, nowMs: number): string {
  const lines: string[] = [];
  lines.push(`${idea.action} ${idea.contract ?? idea.name}${idea.contract ? "" : ` (${idea.symbol})`}`);
  if (idea.desk === "swing") {
    lines.push(`Reference close ${price(idea.entry)}. Plan: buy at tomorrow's open; initial stop 8% below your entry price.`);
  } else {
    lines.push(`Paper entry about ${price(idea.entry)} · Stop ${price(idea.stop)}${idea.target != null ? ` · Target ${price(idea.target)}` : ""}`);
    if (idea.qty != null) lines.push(`Paper size ${idea.qty}${idea.risk != null ? ` · Risk ${inr(idea.risk)}` : ""}`);
  }
  const s = checkSummary(idea.checks);
  lines.push(`Grade: ${eff.grade.toUpperCase()}${s.text ? ` (${s.text})` : ""}`);
  lines.push(`Signalled ${clockTime(idea.signaled_at)} (${humanAge(ageSeconds(nowMs, idea.signaled_at))})`);
  if (eff.reasons.length) lines.push(`Note: ${eff.reasons[0]}`);
  lines.push("A paper idea from Signal Desk. Check the live price and your own risk in your broker app before acting.");
  return lines.join("\n");
}

export function evidenceLine(idea: Idea): string {
  return idea.evidence?.headline ?? "No research record exists for this kind of idea.";
}

