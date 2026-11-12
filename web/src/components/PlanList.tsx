import type { Idea } from "../generated/live";

const ROWS: Array<{ key: "enter" | "stop" | "target" | "trail" | "exit_by"; label: string; cls: string }> = [
  { key: "enter", label: "Enter", cls: "k-entry" },
  { key: "stop", label: "Stop loss", cls: "k-stop" },
  { key: "target", label: "Target", cls: "k-target" },
  { key: "trail", label: "Trail / exit", cls: "k-trail" },
  { key: "exit_by", label: "Time limit", cls: "k-time" },
];

/** The plan in words, in the same colours as the lines on the chart. */
export function PlanList({ plan }: { plan: Idea["plan"] | undefined }) {
  if (!plan) return null;
  const rows = ROWS.filter((r) => plan[r.key]);
  if (!rows.length) return null;
  return (
    <dl class="plan">
      {rows.map((r) => (<><dt key={`${r.key}-t`}><i class={r.cls} />{r.label}</dt><dd key={`${r.key}-d`}>{plan[r.key]}</dd></>))}
    </dl>
  );
}
