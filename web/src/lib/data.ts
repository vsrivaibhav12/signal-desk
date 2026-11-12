// Where the data comes from, and how it is fetched. The page reads the published data branch; it never talks to a broker.
import type { LedgerTrade } from "../generated/ledger-trade";
import type { Live } from "../generated/live";
import type { Manifest } from "../generated/manifest";
import type { Swing } from "../generated/swing";
import type { Intel } from "./types";

export type SourceKind = "live" | "demo";

export interface Source {
  kind: SourceKind;
  /** Folder URL ending in a slash. */
  base: string;
  /** Short text for the top of the page. */
  label: string;
}

const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const RAW = /^https:\/\/raw\.githubusercontent\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/data\/$/;

const rawBase = (repo: string) => `https://raw.githubusercontent.com/${repo}/data/`;

/**
 * Chooses the data source from the address.
 *   ?demo                                          bundled sample data
 *   ?repo=owner/name                               that repository's data branch
 *   ?data=https://raw.githubusercontent.com/o/r/data/   the same, spelled out (no other host is accepted)
 *   https://owner.github.io/name/                  the repository this page is published from
 * Anything else (a local preview, for instance) shows the sample data, clearly labelled.
 */
export function resolveSource(loc: { hostname: string; pathname: string; search: string }, demoBase = "demo/"): Source {
  const q = new URLSearchParams(loc.search);
  const demo: Source = { kind: "demo", base: demoBase, label: "Sample data" };
  if (q.has("demo")) return demo;
  const repo = q.get("repo");
  if (repo && REPO.test(repo)) return { kind: "live", base: rawBase(repo), label: repo };
  const data = q.get("data");
  if (data && RAW.test(data)) return { kind: "live", base: data, label: data.split("/").slice(3, 5).join("/") };
  if (loc.hostname.endsWith(".github.io")) {
    const owner = loc.hostname.split(".")[0] ?? "";
    const name = loc.pathname.split("/").filter(Boolean)[0] ?? "";
    const guess = `${owner}/${name}`;
    if (owner && name && REPO.test(guess)) return { kind: "live", base: rawBase(guess), label: guess };
  }
  return demo;
}

export class FetchError extends Error {}

export async function fetchText(url: string, fetchImpl: typeof fetch = fetch, timeoutMs = 12000): Promise<string> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetchImpl(url, { cache: "no-store", signal: ctl.signal });
    if (!r.ok) throw new FetchError(r.status === 404 ? "not published yet" : `HTTP ${r.status}`);
    return await r.text();
  } catch (err) {
    if (err instanceof FetchError) throw err;
    throw new FetchError(ctl.signal.aborted ? "timed out" : "could not be reached");
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJson<T>(url: string, fetchImpl: typeof fetch): Promise<T> {
  const text = await fetchText(url, fetchImpl);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new FetchError("is not valid data");
  }
}

export interface Snapshot {
  manifest?: Manifest;
  live?: Live;
  swing?: Swing;
  intel?: Intel;
  /** month ("2026-11") -> settled trades, loaded on demand. */
  ledger: Record<string, LedgerTrade[]>;
  /** path -> sha256 of the copy held, so unchanged files are not fetched again. */
  hashes: Record<string, string>;
  /** path -> why the latest fetch failed. A failed fetch keeps the last good copy. */
  errors: Record<string, string>;
  loadedAt: number;
}

export const emptySnapshot = (): Snapshot => ({ ledger: {}, hashes: {}, errors: {}, loadedAt: 0 });

const bust = (url: string, nowMs: number) => `${url}${url.includes("?") ? "&" : "?"}t=${nowMs}`;

/**
 * Brings a snapshot up to date. Reads the manifest first and fetches only the files whose hash changed; with no manifest it fetches
 * them all. Any failure leaves the previous copy of that file in place and records why.
 */
export async function refresh(source: Source, prev: Snapshot, nowMs: number, fetchImpl: typeof fetch = fetch): Promise<Snapshot> {
  const snap: Snapshot = { ...prev, errors: {}, hashes: { ...prev.hashes }, ledger: prev.ledger, loadedAt: nowMs };
  const url = (path: string) => bust(source.base + path, nowMs);

  try {
    snap.manifest = await fetchJson<Manifest>(url("manifest.json"), fetchImpl);
  } catch (err) {
    snap.errors["manifest.json"] = (err as Error).message;
  }
  const entry = (path: string) => snap.manifest?.files.find((f) => f.path === path);
  const unchanged = (path: string, have: unknown) => {
    const e = entry(path);
    return !!e && !!have && snap.hashes[path] === e.sha256;
  };

  const jobs: Array<Promise<void>> = [];
  const get = <K extends "live" | "swing" | "intel">(key: K, path: string, accept: (x: any) => boolean, needed = false) => {
    if (!needed && snap.manifest && !entry(path) && !snap.errors["manifest.json"]) return;      // the branch does not have it (yet)
    if (unchanged(path, snap[key])) return;
    jobs.push(fetchJson<any>(url(path), fetchImpl).then(
      (doc) => {
        if (!accept(doc)) { snap.errors[path] = "was written by a newer version than this page understands"; return; }
        (snap as any)[key] = doc;
        const e = entry(path);
        if (e) snap.hashes[path] = e.sha256;
      },
      (err: Error) => { snap.errors[path] = err.message; }));
  };
  get("live", "live.json", (d) => d?.schema === 1, true);
  get("swing", "swing.json", (d) => d?.schema === 1);
  get("intel", "intelligence.json", (d) => d?.schema === 1);
  await Promise.all(jobs);
  return snap;
}

const LEDGER_FILE = /^ledger(_swing)?-(\d{4}-\d{2})\.jsonl$/;

/** Parses one ledger file. Lines that are not valid are skipped, never fatal. */
export function parseLedger(text: string): LedgerTrade[] {
  const out: LedgerTrade[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      const row = JSON.parse(t) as LedgerTrade;
      if (row && typeof row === "object" && typeof row.id === "string") out.push(row);
    } catch { /* a torn line */ }
  }
  return out;
}

/** Loads the settled trades of the latest `months` months (both the live engine's ledger and the swing desk's). */
export async function loadLedger(source: Source, snap: Snapshot, nowMs: number, months = 3, fetchImpl: typeof fetch = fetch): Promise<Snapshot> {
  const files = (snap.manifest?.files ?? []).filter((f) => LEDGER_FILE.test(f.path));
  const wanted = [...new Set(files.map((f) => LEDGER_FILE.exec(f.path)![2]!))].sort().slice(-months);
  const ledger: Record<string, LedgerTrade[]> = { ...snap.ledger };
  const hashes = { ...snap.hashes };
  const errors = { ...snap.errors };
  await Promise.all(files.filter((f) => wanted.includes(LEDGER_FILE.exec(f.path)![2]!)).map(async (f) => {
    if (hashes[f.path] === f.sha256) return;
    try {
      ledger[f.path] = parseLedger(await fetchText(bust(source.base + f.path, nowMs), fetchImpl));
      hashes[f.path] = f.sha256;
    } catch (err) {
      errors[f.path] = (err as Error).message;
    }
  }));
  return { ...snap, ledger, hashes, errors };
}

export function allLedgerTrades(snap: Snapshot): LedgerTrade[] {
  return Object.values(snap.ledger).flat().sort((a, b) => (a.entry_time < b.entry_time ? -1 : 1));
}
