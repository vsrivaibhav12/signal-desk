// Can the page's numbers be acted on right now? One answer, used by every screen.
import type { Live } from "../generated/live";
import { ageSeconds, DEFAULT_THRESHOLDS, humanAge } from "./time";

export type TrustState = "ok" | "degraded" | "stale" | "closed" | "offline";

export interface SessionWindow { name: string; start: string; end: string }

const IST_OFFSET_MS = 19_800_000;
const minutesOf = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));

/** The market session that should be running at `nowMs` (India time, Monday to Friday), or null. Exchange holidays are not known here. */
export function sessionDueNow(nowMs: number, windows: SessionWindow[] | undefined): SessionWindow | null {
  if (!windows?.length) return null;
  const d = new Date(nowMs + IST_OFFSET_MS);
  const weekday = d.getUTCDay();
  if (weekday === 0 || weekday === 6) return null;
  const minute = d.getUTCHours() * 60 + d.getUTCMinutes();
  return windows.find((w) => minute >= minutesOf(w.start) && minute <= minutesOf(w.end)) ?? null;
}

export interface Trust {
  state: TrustState;
  /** Plain-language reasons, shown in the banner. */
  reasons: string[];
  priceAge: number | null;
  publishAge: number | null;
}

/**
 * ok        the engine is watching, prices are recent and every input it needs is present.
 * degraded  the engine is up but something it relies on is missing or ageing (its own health check says WATCH, prices are amber, a login is pending).
 * stale     the engine says it is watching but has not published, or prices are old. Nothing here is actionable.
 * closed    no market session is running; the numbers are the last values, not live ones.
 * offline   nothing has been received at all.
 */
export function assessTrust(live: Live | undefined, nowMs: number): Trust {
  if (!live) return { state: "offline", reasons: ["No data has been received yet."], priceAge: null, publishAge: null };
  const th = live.thresholds ?? DEFAULT_THRESHOLDS;
  const publishAge = ageSeconds(nowMs, live.generated_at);
  const priceAge = ageSeconds(nowMs, live.freshness.prices_asof);
  const phase = live.session.phase ?? null;

  if (phase === "login") {
    return { state: "degraded", reasons: ["The engine is waiting for a broker login. No new prices are arriving."], priceAge, publishAge };
  }
  if (phase === "starting" && publishAge !== null && publishAge < 600) {
    return { state: "degraded", reasons: ["The engine is starting up and has not fetched prices yet."], priceAge, publishAge };
  }
  if (phase !== "watching") {
    // Closed only when no session is due. If one should be running but the engine has not published, say so: that is a fault, not a holiday.
    const due = sessionDueNow(nowMs, live.session_windows);
    if (due && (publishAge === null || publishAge > th.price_stale_s)) {
      return {
        state: "stale",
        reasons: [`A market session should be running now (${due.name} session, ${due.start} to ${due.end} India time), but the engine's last update was ${humanAge(publishAge)}. `
          + "It has probably not started. On a market holiday this is expected; otherwise open the Actions tab on GitHub and start the Trade engine."],
        priceAge, publishAge,
      };
    }
    return { state: "closed", reasons: ["No market session is running. These are the last values from the previous session."], priceAge, publishAge };
  }

  const reasons: string[] = [];
  if (publishAge === null || publishAge > th.price_stale_s) {
    reasons.push(`The engine has not published for ${humanAge(publishAge).replace(" ago", "")}.`);
  }
  if (priceAge === null) {
    reasons.push("The newest price time is unknown.");
  } else if (priceAge > th.price_stale_s) {
    reasons.push(`Prices are ${humanAge(priceAge).replace(" ago", "")} old.`);
  }
  if (reasons.length) return { state: "stale", reasons, priceAge, publishAge };

  if (live.health.decision === "WATCH") {
    const blocking = live.health.blocking.length ? live.health.blocking.join(", ") : "an input it depends on";
    reasons.push(`The engine's own data check says WATCH (${blocking}), so it will not grade anything as TAKE.`);
  }
  if (priceAge !== null && priceAge > th.price_amber_s) {
    reasons.push(`Prices are ${humanAge(priceAge).replace(" ago", "")} old.`);
  }
  return { state: reasons.length ? "degraded" : "ok", reasons, priceAge, publishAge };
}
