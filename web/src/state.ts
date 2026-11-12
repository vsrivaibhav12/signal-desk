// The page's state: the latest data, the current time, and whether the numbers can be trusted.
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { emptySnapshot, loadLedger, refresh, resolveSource, type Snapshot, type Source } from "./lib/data";
import { assessTrust, type Trust } from "./lib/trust";

export const REFRESH_MS = 60_000;
const TICK_MS = 15_000;

export interface AppState {
  source: Source;
  snap: Snapshot;
  /** "Now" for every age on the page. In the sample data it is fixed just after the sample was made, so the sample looks live. */
  nowMs: number;
  trust: Trust;
  busy: boolean;
  /** True when the sample data is being shown as if it had gone stale (?demo&stale). */
  simulatedStale: boolean;
  refreshNow: () => void;
  ensureLedger: () => Promise<void>;
}

export function useAppState(): AppState {
  const source = useMemo(() => resolveSource(window.location, `${import.meta.env.BASE_URL}demo/`), []);
  const simulatedStale = source.kind === "demo" && new URLSearchParams(window.location.search).has("stale");
  const [snap, setSnap] = useState<Snapshot>(emptySnapshot());
  const [clock, setClock] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const latest = useRef(snap);
  latest.current = snap;
  const inflight = useRef(false);

  const refreshNow = useCallback(() => {
    if (inflight.current) return;
    inflight.current = true;
    setBusy(true);
    const started = Date.now();
    refresh(source, latest.current, started)
      .then((next) => setSnap(next))
      .catch(() => { /* refresh() reports its own failures in snap.errors; anything else leaves the last data in place */ })
      .finally(() => { inflight.current = false; setBusy(false); setClock(Date.now()); });
  }, [source]);

  const ensureLedger = useCallback(async () => {
    const next = await loadLedger(source, latest.current, Date.now());
    setSnap(next);
  }, [source]);

  useEffect(() => {
    refreshNow();
    const poll = setInterval(() => { if (document.visibilityState === "visible") refreshNow(); }, REFRESH_MS);
    const tick = setInterval(() => setClock(Date.now()), TICK_MS);
    const onVisible = () => { if (document.visibilityState === "visible") { setClock(Date.now()); refreshNow(); } };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(poll); clearInterval(tick); document.removeEventListener("visibilitychange", onVisible); };
  }, [refreshNow]);

  const nowMs = source.kind === "demo" && snap.live ? Date.parse(snap.live.generated_at) + (simulatedStale ? 1_500_000 : 90_000) : clock;
  const trust = useMemo(() => assessTrust(snap.live, nowMs), [snap.live, nowMs]);
  return { source, snap, nowMs, trust, busy, simulatedStale, refreshNow, ensureLedger };
}

export function useHash(): [string, (h: string) => void] {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "home").split("?")[0]!;
  const [hash, setHash] = useState(read);
  useEffect(() => {
    const on = () => setHash(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [hash, (h) => { window.location.hash = `#/${h}`; }];
}

/** A preference that lives in this browser only. Never used for anything that must be right. */
export function useLocalPref<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch { return initial; }
  });
  return [value, (v) => {
    setValue(v);
    try { window.localStorage.setItem(key, JSON.stringify(v)); } catch { /* private window: the preference simply is not remembered */ }
  }];
}
