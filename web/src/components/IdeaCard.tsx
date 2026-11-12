import type { Live } from "../generated/live";
import { dash, price, inr } from "../lib/format";
import { checkSummary, evidenceLine, type Ranked } from "../lib/ideas";
import { ageSeconds, humanAge } from "../lib/time";
import { CheckDots, deskName, GradeBadge } from "./ui";

export function IdeaCard({ r, live, nowMs, onOpen }: { r: Ranked; live: Live | undefined; nowMs: number; onOpen: (id: string) => void }) {
  const { idea, eff } = r;
  const sw = idea.desk === "swing";
  const summary = checkSummary(idea.checks);
  const age = ageSeconds(nowMs, idea.signaled_at);
  return (
    <button type="button" class={`idea ${eff.grade}`} onClick={() => onOpen(idea.id)} aria-label={`${idea.name}, ${idea.action}, ${eff.grade}. Open details.`}>
      <div class="idea-top">
        <span class="idea-name">{idea.name}</span>
        <span class={`side ${idea.action === "BUY" ? "buy" : "sell"}`}>{idea.action}</span>
        <GradeBadge eff={eff} />
        <span class="desk-tag grow">{deskName(live, idea.desk)}</span>
        <span class="muted small">{humanAge(age)}</span>
      </div>
      {idea.contract && <div class="muted small" style="margin-top:2px">{idea.contract}</div>}
      <div class="nums">
        <div><span class="l">{sw ? "Ref. close" : "Entry"}</span><span class="v">{price(idea.entry)}</span></div>
        <div><span class="l">Stop</span><span class="v">{price(idea.stop)}</span></div>
        <div><span class="l">Target</span><span class="v">{idea.target != null ? price(idea.target) : sw ? "Trail" : dash}</span></div>
        <div><span class="l">{sw ? "Size" : "Risk"}</span><span class="v">{sw ? "Yours" : idea.risk != null ? inr(idea.risk) : dash}</span></div>
      </div>
      <div class="idea-meta">
        <CheckDots checks={idea.checks} />
        {summary.text && <span>{summary.text}</span>}
        {idea.paper_position && !sw && <span>Paper position open</span>}
        {idea.cost_ratio != null && <span>Costs {Math.round(idea.cost_ratio * 100)}% of risk</span>}
      </div>
      {eff.reasons[0] && eff.grade !== "take" && <div class="reason">{eff.reasons[0]}</div>}
      {eff.grade === "watch" && eff.stated === "watch" && idea.blockers[0] && <div class="reason">{idea.blockers[0]}</div>}
      <div class="evidence">{evidenceLine(idea)}</div>
    </button>
  );
}
