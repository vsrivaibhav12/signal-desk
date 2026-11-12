import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { emptySnapshot, loadLedger, parseLedger, refresh, resolveSource, type Snapshot } from "../src/lib/data";
import { decryptJournal, journalStats, JournalError } from "../src/lib/journalCrypto";
import { makeLive, NOW } from "./helpers";

const loc = (hostname: string, pathname = "/", search = "") => ({ hostname, pathname, search });

describe("where the data comes from", () => {
  it("a GitHub Pages address maps to that repository's data branch", () => {
    expect(resolveSource(loc("someone.github.io", "/signal-desk/"))).toEqual({
      kind: "live", base: "https://raw.githubusercontent.com/someone/signal-desk/data/", label: "someone/signal-desk" });
  });

  it("?demo wins, and an unknown host shows the sample data", () => {
    expect(resolveSource(loc("someone.github.io", "/signal-desk/", "?demo")).kind).toBe("demo");
    expect(resolveSource(loc("localhost", "/")).kind).toBe("demo");
    expect(resolveSource(loc("someone.github.io", "/")).kind).toBe("demo");      // a user site with no repository in the path
  });

  it("?repo= selects a repository, but only a well-formed one", () => {
    expect(resolveSource(loc("localhost", "/", "?repo=me/desk")).base).toBe("https://raw.githubusercontent.com/me/desk/data/");
    expect(resolveSource(loc("localhost", "/", "?repo=me/desk/../x")).kind).toBe("demo");
    expect(resolveSource(loc("localhost", "/", "?repo=evil.com")).kind).toBe("demo");
  });

  it("?data= accepts only a raw.githubusercontent.com data-branch folder, never another host", () => {
    const good = "https://raw.githubusercontent.com/me/desk/data/";
    expect(resolveSource(loc("localhost", "/", `?data=${encodeURIComponent(good)}`))).toMatchObject({ kind: "live", base: good });
    for (const bad of ["https://evil.example/me/desk/data/", "https://raw.githubusercontent.com/me/desk/main/", "http://raw.githubusercontent.com/me/desk/data/",
                       "https://raw.githubusercontent.com.evil.example/me/desk/data/", "https://raw.githubusercontent.com/me/desk/data/../../x/"]) {
      expect(resolveSource(loc("localhost", "/", `?data=${encodeURIComponent(bad)}`)).kind).toBe("demo");
    }
  });
});

// A fake network: path -> body (object or string) or a status number for a failure. Counts every request.
function network(files: Record<string, unknown>) {
  const calls: string[] = [];
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const path = url.slice("https://x/".length).split("?")[0]!;
    calls.push(path);
    const body = files[path];
    if (typeof body === "number") return new Response("", { status: body });
    if (body === undefined) return new Response("", { status: 404 });
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status: 200 });
  }) as typeof fetch;
  return { impl, calls };
}
const source = { kind: "live" as const, base: "https://x/", label: "x" };
const manifest = (files: Array<[string, string]>) => ({ contract: 1, generated_at: "2026-11-11T10:00:00Z",
  files: files.map(([path, sha256]) => ({ path, bytes: 1, sha256: sha256.padEnd(64, "0"), updated_at: "2026-11-11T10:00:00Z", schema: null })) });

describe("refreshing", () => {
  it("fetches what the manifest lists and skips what it does not", async () => {
    const net = network({ "manifest.json": manifest([["live.json", "a"]]), "live.json": makeLive() });
    const snap = await refresh(source, emptySnapshot(), NOW, net.impl);
    expect(snap.live?.schema).toBe(1);
    expect(net.calls.sort()).toEqual(["live.json", "manifest.json"]);          // swing.json and intelligence.json are not published, so not requested
    expect(snap.errors).toEqual({});
  });

  it("a file whose hash has not changed is not fetched again", async () => {
    const files = { "manifest.json": manifest([["live.json", "a"]]), "live.json": makeLive() };
    const first = await refresh(source, emptySnapshot(), NOW, network(files).impl);
    const net = network(files);
    const second = await refresh(source, first, NOW + 60000, net.impl);
    expect(net.calls).toEqual(["manifest.json"]);
    expect(second.live).toBe(first.live);
    const changed = network({ "manifest.json": manifest([["live.json", "b"]]), "live.json": makeLive({ date: "2026-11-12" }) });
    expect((await refresh(source, second, NOW + 120000, changed.impl)).live?.date).toBe("2026-11-12");
  });

  it("without a manifest it fetches the files directly", async () => {
    const net = network({ "manifest.json": 404, "live.json": makeLive(), "swing.json": 404, "intelligence.json": 404 });
    const snap = await refresh(source, emptySnapshot(), NOW, net.impl);
    expect(snap.live).toBeDefined();
    expect(snap.errors["manifest.json"]).toBe("not published yet");
    expect(net.calls).toContain("swing.json");
  });

  it("a failed fetch keeps the last good copy and says why", async () => {
    const files = { "manifest.json": manifest([["live.json", "a"]]), "live.json": makeLive() };
    const first = await refresh(source, emptySnapshot(), NOW, network(files).impl);
    const broken = network({ "manifest.json": manifest([["live.json", "b"]]), "live.json": 500 });
    const next = await refresh(source, first, NOW + 60000, broken.impl);
    expect(next.live).toBe(first.live);
    expect(next.errors["live.json"]).toBe("HTTP 500");
  });

  it("a file from a newer contract is refused rather than misread", async () => {
    const net = network({ "manifest.json": manifest([["live.json", "a"]]), "live.json": { ...makeLive(), schema: 2 } });
    const snap = await refresh(source, emptySnapshot(), NOW, net.impl);
    expect(snap.live).toBeUndefined();
    expect(snap.errors["live.json"]).toMatch(/newer version/);
  });

  it("garbage and network errors are reported, not thrown", async () => {
    const garbage = network({ "manifest.json": "{not json", "live.json": "<html>" });
    const snap = await refresh(source, emptySnapshot(), NOW, garbage.impl);
    expect(snap.errors["live.json"]).toBe("is not valid data");
    const offline = (async () => { throw new TypeError("network down"); }) as unknown as typeof fetch;
    expect((await refresh(source, emptySnapshot(), NOW, offline)).errors["live.json"]).toBe("could not be reached");
  });
});

describe("the ledger", () => {
  const row = (id: string, t: string) => JSON.stringify({ id, entry_time: t, desk: "swing", net: 1 });

  it("skips torn lines and rows without an id", () => {
    const text = [row("a", "2026-11-01"), '{"id":"torn', "", "not json", '{"nothing":1}', row("b", "2026-11-02")].join("\n");
    expect(parseLedger(text).map((r) => r.id)).toEqual(["a", "b"]);
  });

  it("loads the latest months of both ledgers and does not refetch an unchanged file", async () => {
    const files = {
      "ledger-2026-09.jsonl": row("old", "2026-09-01"), "ledger-2026-10.jsonl": row("oct", "2026-10-01"), "ledger-2026-11.jsonl": row("nov", "2026-11-01"),
      "ledger_swing-2026-11.jsonl": row("sw", "2026-11-03"), "ledger-2026-08.jsonl": row("older", "2026-08-01"),
    };
    const m = manifest(Object.keys(files).map((p, i) => [p, String(i + 1)] as [string, string]));
    const base: Snapshot = { ...emptySnapshot(), manifest: m as never };
    const net = network(files);
    const snap = await loadLedger(source, base, NOW, 3, net.impl);
    expect(Object.keys(snap.ledger).sort()).toEqual(["ledger-2026-09.jsonl", "ledger-2026-10.jsonl", "ledger-2026-11.jsonl", "ledger_swing-2026-11.jsonl"]);
    const again = network(files);
    await loadLedger(source, snap, NOW + 1, 3, again.impl);
    expect(again.calls).toEqual([]);
  });
});

describe("the journal", () => {
  const fixture = JSON.parse(readFileSync(join(process.cwd(), "test", "fixtures", "journal.bin"), "utf8"));
  const subtle = webcrypto.subtle as SubtleCrypto;

  it("decrypts what the engine wrote (the Python and browser formats agree)", async () => {
    const doc = await decryptJournal("correct horse battery staple", fixture, subtle);
    expect(doc.entries.map((e) => e.name)).toEqual(["HAL", "Nifty 50 CE"]);
    expect(doc.entries[0]).toMatchObject({ took: true, fill_price: 4010, slippage_pct: 0.25 });
  });

  it("a wrong passphrase, a tampered file and a stranger's file are all refused", async () => {
    await expect(decryptJournal("nope", fixture, subtle)).rejects.toBeInstanceOf(JournalError);
    await expect(decryptJournal("correct horse battery staple", { ...fixture, ct: fixture.ct.slice(0, -6) + "AAAAAA" }, subtle)).rejects.toBeInstanceOf(JournalError);
    await expect(decryptJournal("x", { hello: "world" }, subtle)).rejects.toBeInstanceOf(JournalError);
    await expect(decryptJournal("x", null, subtle)).rejects.toBeInstanceOf(JournalError);
  });

  it("will not run a hostile number of key-derivation rounds", async () => {
    await expect(decryptJournal("x", { ...fixture, iter: 2_000_000_000 }, subtle)).rejects.toThrow(/unsupported key settings/);
    await expect(decryptJournal("x", { ...fixture, iter: 1 }, subtle)).rejects.toThrow(/unsupported key settings/);
  });

  it("summarises how much worse your fills were than the paper price", async () => {
    const doc = await decryptJournal("correct horse battery staple", fixture, subtle);
    expect(journalStats(doc.entries)).toEqual({ took: 1, skipped: 1, withFill: 1, meanSlippagePct: 0.25 });
    expect(journalStats([])).toEqual({ took: 0, skipped: 0, withFill: 0, meanSlippagePct: null });
  });
});
