import { useEffect, useState } from "react";

// Minimal hash router: works behind nginx/vite with no server rewrites and no dependency.
export type Route =
  | { name: "overview" }
  | { name: "runs" }
  | { name: "run"; id: string }
  | { name: "patches" }
  | { name: "patch"; id: string }
  | { name: "demo" }
  | { name: "not_found"; path: string };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, "").replace(/\/+$/, "") || "/";
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts.length === 0) return { name: "overview" };
  if (parts[0] === "runs" && parts.length === 1) return { name: "runs" };
  if (parts[0] === "runs" && parts.length === 2) return { name: "run", id: parts[1] };
  if (parts[0] === "patches" && parts.length === 1) return { name: "patches" };
  if (parts[0] === "patches" && parts.length === 2) return { name: "patch", id: parts[1] };
  if (parts[0] === "demo") return { name: "demo" };
  return { name: "not_found", path };
}

export const href = {
  overview: () => "#/",
  runs: () => "#/runs",
  run: (id: string) => `#/runs/${encodeURIComponent(id)}`,
  patches: () => "#/patches",
  patch: (id: string) => `#/patches/${encodeURIComponent(id)}`,
  demo: () => "#/demo",
};

export function navigate(to: string) {
  window.location.hash = to.replace(/^#/, "");
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => { setRoute(parseRoute(window.location.hash)); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
