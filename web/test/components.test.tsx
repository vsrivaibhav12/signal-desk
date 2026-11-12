import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/app";
import { IdeaCard } from "../src/components/IdeaCard";
import { IdeaSheet } from "../src/components/IdeaSheet";
import { Banner } from "../src/components/ui";
import { effectiveIdea } from "../src/lib/ideas";
import { assessTrust } from "../src/lib/trust";
import { iso, makeApp, makeChart, makeIdea, makeLive, NOW } from "./helpers";

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); window.location.hash = ""; });

const ranked = (over = {}, liveOver = {}) => {
  const live = makeLive(liveOver);
  const idea = makeIdea(over);
  return { live, r: { idea, eff: effectiveIdea(idea, assessTrust(live, NOW), NOW) } };
};

describe("an idea card", () => {
  it("shows a fresh TAKE with its checks and the research line", () => {
    const { live, r } = ranked();
    render(<IdeaCard r={r} app={makeApp(live)} onOpen={() => {}} />);
    expect(screen.getByText("TAKE")).toBeTruthy();
    expect(screen.getByText("4 of 4 checks passed")).toBeTruthy();
    expect(screen.getByText(/Replay, after costs/)).toBeTruthy();
    expect(screen.getByText("2 min ago")).toBeTruthy();
  });

  it("an old TAKE is shown as EXPIRED with the reason, never as TAKE", () => {
    const { live, r } = ranked({ signaled_at: iso(3000) });
    render(<IdeaCard r={r} app={makeApp(live)} onOpen={() => {}} />);
    expect(screen.queryByText("TAKE")).toBeNull();
    expect(screen.getByText("EXPIRED")).toBeTruthy();
    expect(screen.getByText(/moved on/)).toBeTruthy();
  });

  it("opens its details when pressed", () => {
    const { live, r } = ranked();
    const open = vi.fn();
    render(<IdeaCard r={r} app={makeApp(live)} onOpen={open} />);
    fireEvent.click(screen.getByRole("button"));
    expect(open).toHaveBeenCalledWith(r.idea.id);
  });

  it("shows a check that could not be verified as such", () => {
    const { live, r } = ranked({ grade: "watch", checks: [{ id: "vol", label: "Heavy volume", state: "unknown" }, { id: "cost", label: "Costs", state: "pass" }], blockers: ["Heavy volume: could not be verified"] });
    render(<IdeaCard r={r} app={makeApp(live)} onOpen={() => {}} />);
    expect(screen.getByText("1 of 2 checks passed, 1 not verified")).toBeTruthy();
    expect(screen.getByText("Heavy volume: could not be verified")).toBeTruthy();
  });
});

describe("the data banner", () => {
  it("says not to act when the data is stale, and is silent when everything is fine", () => {
    const stale = assessTrust(makeLive({ generated_at: iso(2000) }), NOW);
    const { container, rerender } = render(<Banner trust={stale} demo={false} simulatedStale={false} />);
    expect(container.textContent).toMatch(/Do not act on what you see here/);
    expect(container.textContent).toMatch(/TAKE badges are removed/);
    rerender(<Banner trust={assessTrust(makeLive(), NOW)} demo={false} simulatedStale={false} />);
    expect(container.querySelector(".banner")).toBeNull();
  });
});

describe("the idea sheet", () => {
  it("lists the checks, explains an unverified one, and closes on Escape", () => {
    const { live, r } = ranked({ grade: "watch", checks: [{ id: "vol", label: "Heavy volume", state: "unknown" }, { id: "mkt", label: "Nifty agrees", state: "fail" }] });
    const close = vi.fn();
    render(<IdeaSheet r={r} app={makeApp(live)} onClose={close} />);
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText(/could not be verified, which counts as not passing/)).toBeTruthy();
    expect(screen.getByText(/\(failed\)/)).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(close).toHaveBeenCalled();
  });

  it("copies the order notes, and says plainly when copying is not possible", async () => {
    const { live, r } = ranked();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<IdeaSheet r={r} app={makeApp(live)} onClose={() => {}} />);
    fireEvent.click(screen.getByText("Copy notes"));
    await waitFor(() => expect(screen.getByText("Copied.")).toBeTruthy());
    expect(writeText.mock.calls[0]![0]).toContain("BUY HAL (NSE:HAL-EQ)");
    writeText.mockRejectedValueOnce(new Error("denied"));
    fireEvent.click(screen.getByText("Copy notes"));
    await waitFor(() => expect(screen.getByText(/Could not copy/)).toBeTruthy());
  });

  it("states what the research says and that a TAKE is not a promise", () => {
    const { live, r } = ranked();
    render(<IdeaSheet r={r} app={makeApp(live)} onClose={() => {}} />);
    expect(screen.getByText(/does not mean it is expected to make money/)).toBeTruthy();
  });
});

describe("the whole page on the sample data", () => {
  const demo = (name: string) => readFileSync(join(process.cwd(), "public", "demo", name), "utf8");
  const stubFetch = () => vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const name = String(url).split("?")[0]!.split("/").pop()!;
    try { return new Response(demo(name), { status: 200 }); } catch { return new Response("", { status: 404 }); }
  }));

  it("loads, ranks TAKEs above watch signals and opens an idea", async () => {
    stubFetch();
    window.history.replaceState({}, "", "/?demo");
    render(<App />);
    await waitFor(() => expect(screen.getByText("Act now")).toBeTruthy(), { timeout: 3000 });
    expect(screen.getByText("Sample data")).toBeTruthy();
    const badges = [...document.querySelectorAll(".idea .badge")].map((b) => b.textContent);
    expect(badges.indexOf("TAKE")).toBe(0);
    expect(badges.lastIndexOf("TAKE")).toBeLessThan(badges.indexOf("WATCH"));
    expect(screen.getByText("Watching")).toBeTruthy();
    fireEvent.click(document.querySelector(".idea") as HTMLElement);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("as if the engine had stopped, no TAKE survives", async () => {
    stubFetch();
    window.history.replaceState({}, "", "/?demo&stale");
    render(<App />);
    await waitFor(() => expect(screen.getByText("Act now")).toBeTruthy(), { timeout: 3000 });
    expect(screen.getByText(/Do not act on what you see here/)).toBeTruthy();
    expect(screen.getByText(/The engine has not published for/)).toBeTruthy();
    expect([...document.querySelectorAll(".idea .badge")].some((b) => b.textContent === "TAKE")).toBe(false);
    expect(screen.getAllByText("EXPIRED").length).toBeGreaterThan(0);
  });

  it("shows a calm message instead of a blank page when nothing can be fetched", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));
    window.history.replaceState({}, "", "/?demo");
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Could not load data/)).toBeTruthy(), { timeout: 3000 });
    expect(screen.getByText("No data")).toBeTruthy();
  });
});
