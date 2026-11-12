import { useEffect, useMemo, useState } from "preact/hooks";
import { IdeaSheet } from "./components/IdeaSheet";
import { Banner, TrustPill } from "./components/ui";
import { Home, rankFor } from "./screens/Home";
import { JournalScreen } from "./screens/JournalScreen";
import { Market } from "./screens/Market";
import { Positions } from "./screens/Positions";
import { SwingScreen } from "./screens/SwingScreen";
import { SystemScreen } from "./screens/SystemScreen";
import { useAppState, useHash, useLocalPref } from "./state";

const TABS: Array<{ id: string; label: string; icon: string }> = [
  { id: "home", label: "Today", icon: "◎" },
  { id: "positions", label: "Positions", icon: "▤" },
  { id: "market", label: "Market", icon: "◭" },
  { id: "swing", label: "Swing", icon: "↗" },
  { id: "journal", label: "Journal", icon: "✎" },
  { id: "system", label: "System", icon: "⚙" },
];

type Theme = "auto" | "light" | "dark";
const NEXT_THEME: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };

export function App() {
  const app = useAppState();
  const [route] = useHash();
  const [theme, setTheme] = useLocalPref<Theme>("sd_theme", "auto");
  const [openId, setOpenId] = useState<string | null>(null);
  const ranked = useMemo(() => rankFor(app), [app.snap.live, app.snap.swing, app.trust, app.nowMs]);
  const selected = openId ? ranked.find((r) => r.idea.id === openId) : undefined;
  const tab = TABS.some((t) => t.id === route) ? route : "home";

  useEffect(() => {
    if (theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => { document.title = tab === "home" ? "Signal Desk" : `${TABS.find((t) => t.id === tab)?.label} · Signal Desk`; }, [tab]);

  const demo = app.source.kind === "demo";
  return (
    <>
      <header class="topbar">
        <div class="topbar-in">
          <div class="brand">
            <svg viewBox="0 0 100 100" aria-hidden="true"><rect width="100" height="100" rx="22" fill="var(--accent)" /><path d="M18 66 38 44 54 58 80 30" fill="none" stroke="var(--accent-ink)" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" /></svg>
            Signal Desk
          </div>
          <nav class="nav" aria-label="Main">
            {TABS.map((t) => <a key={t.id} href={`#/${t.id}`} aria-current={tab === t.id ? "page" : undefined}>{t.label}</a>)}
          </nav>
          <span class="grow" />
          <TrustPill trust={app.trust} demo={demo} />
          <button type="button" class="iconbtn" onClick={() => app.refreshNow()} disabled={app.busy} aria-label="Refresh now">{app.busy ? "…" : "Refresh"}</button>
          <button type="button" class="iconbtn" onClick={() => setTheme(NEXT_THEME[theme])} aria-label={`Theme: ${theme}. Switch.`}>{theme === "auto" ? "Auto" : theme === "light" ? "Light" : "Dark"}</button>
        </div>
      </header>

      <main>
        <Banner trust={app.trust} demo={demo} simulatedStale={app.simulatedStale} />
        {tab === "home" && <Home app={app} ranked={ranked} onOpen={setOpenId} />}
        {tab === "positions" && <Positions app={app} openable={new Set(ranked.map((r) => r.idea.id))} onOpen={setOpenId} />}
        {tab === "market" && <Market app={app} />}
        {tab === "swing" && <SwingScreen app={app} />}
        {tab === "journal" && <JournalScreen app={app} />}
        {tab === "system" && <SystemScreen app={app} />}
      </main>

      <div class="footer">Paper-trading research software. It reads market data and places no orders. A TAKE means an idea passed this tool's own checks, not that it will make money.</div>

      <nav class="tabbar" aria-label="Main">
        {TABS.map((t) => <a key={t.id} href={`#/${t.id}`} aria-current={tab === t.id ? "page" : undefined}><span class="ic" aria-hidden="true">{t.icon}</span>{t.label}</a>)}
      </nav>

      {selected && <IdeaSheet r={selected} app={app} onClose={() => setOpenId(null)} />}
    </>
  );
}
