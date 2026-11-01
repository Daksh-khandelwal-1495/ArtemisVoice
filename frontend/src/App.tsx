import { useEffect, useState } from "react";
import { baseUrl, health, studioUiUrl } from "./api";
import { Empty } from "./components/ui";
import { href, Route, useRoute } from "./router";
import { Demo } from "./views/Demo";
import { Overview } from "./views/Overview";
import { PatchDetail, Patches } from "./views/Patches";
import { RunDetail } from "./views/RunDetail";
import { Runs } from "./views/Runs";

type Theme = "light" | "dark" | "system";
const THEME_KEY = "artemis.theme";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try { const saved = localStorage.getItem(THEME_KEY); if (saved === "light" || saved === "dark") return saved; } catch { /* ignore */ }
    return "system";
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { if (theme === "system") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }, [theme]);
  const cycle = () => setTheme((current) => (current === "system" ? "light" : current === "light" ? "dark" : "system"));
  return [theme, cycle];
}

function ApiStatus() {
  const [status, setStatus] = useState<"checking" | "up" | "down">("checking");
  useEffect(() => {
    let alive = true;
    const ping = () => health().then(() => alive && setStatus("up"), () => alive && setStatus("down"));
    ping();
    const timer = window.setInterval(ping, 15000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  return <div className={`environment api-${status}`} title={`Artemis API at ${baseUrl}`}><i /> {status === "up" ? "Artemis API" : status === "down" ? "API offline" : "Connecting…"}<span>{baseUrl}</span></div>;
}

const NAV: { key: string; label: string; to: string; match: Route["name"][] }[] = [
  { key: "overview", label: "Overview", to: href.overview(), match: ["overview"] },
  { key: "runs", label: "VoiceFlow runs", to: href.runs(), match: ["runs", "run"] },
  { key: "patches", label: "Patches", to: href.patches(), match: ["patches", "patch"] },
  { key: "demo", label: "Demo", to: href.demo(), match: ["demo"] },
];

function View({ route }: { route: Route }) {
  switch (route.name) {
    case "overview": return <Overview />;
    case "runs": return <Runs />;
    case "run": return <RunDetail key={route.id} id={route.id} />;
    case "patches": return <Patches />;
    case "patch": return <PatchDetail key={route.id} id={route.id} />;
    case "demo": return <Demo />;
    default: return <div className="page"><Empty title="Page not found"><a href={href.overview()}>Back to overview</a></Empty></div>;
  }
}

export function App() {
  const route = useRoute();
  const [theme, cycleTheme] = useTheme();
  return <>
    <header className="topbar">
      <a className="brand" href={href.overview()} aria-label="ArtemisVoice home"><span className="brand-mark">A</span> ARTEMIS<span>VOICE</span></a>
      <nav className="nav" aria-label="Primary">{NAV.map((item) => <a key={item.key} href={item.to} className={item.match.includes(route.name) ? "active" : undefined} aria-current={item.match.includes(route.name) ? "page" : undefined}>{item.label}</a>)}</nav>
      <div className="topbar-right">
        <ApiStatus />
        <a className="icon-btn" href={studioUiUrl} target="_blank" rel="noreferrer" title="Open VoiceFlow Studio">Studio ↗</a>
        <button className="icon-btn" onClick={cycleTheme} title={`Theme: ${theme}`} aria-label={`Theme: ${theme}. Click to change.`}>{theme === "light" ? "☀" : theme === "dark" ? "☾" : "◐"}</button>
      </div>
    </header>
    <main><View route={route} /></main>
  </>;
}
