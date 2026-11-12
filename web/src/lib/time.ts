// Ages and freshness. Every number on the page carries its age; these decide how it is shown.

export type Freshness = "fresh" | "amber" | "stale" | "unknown";

export interface Thresholds {
  price_amber_s: number;
  price_stale_s: number;
  idea_expire_s: number;
}

export const DEFAULT_THRESHOLDS: Thresholds = { price_amber_s: 300, price_stale_s: 600, idea_expire_s: 900 };

/** Seconds between a timestamp and `nowMs`, or null if there is no usable timestamp. Positive means the timestamp is in the past. */
export function ageSeconds(nowMs: number, iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.round((nowMs - t) / 1000);
}

/**
 * How trustworthy an age is. A timestamp slightly in the future (clock skew of up to a minute) counts as fresh; further ahead is not
 * believable and is "unknown", never "fresh".
 */
export function freshness(age: number | null, th: Thresholds = DEFAULT_THRESHOLDS): Freshness {
  if (age === null) return "unknown";
  if (age < -60) return "unknown";
  if (age < th.price_amber_s) return "fresh";
  if (age < th.price_stale_s) return "amber";
  return "stale";
}

/** "just now", "3 min ago", "1 h 05 min ago", "2 d ago". */
export function humanAge(age: number | null): string {
  if (age === null) return "time unknown";
  if (age < -60) return "time unknown";
  if (age < 45) return "just now";
  const min = Math.round(age / 60);
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ${String(min % 60).padStart(2, "0")} min ago`;
  return `${Math.floor(h / 24)} d ago`;
}
