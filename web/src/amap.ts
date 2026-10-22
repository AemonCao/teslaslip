export interface AMapTextStyle {
  color: string;
  background: string;
  border: string;
  "font-size": string;
  "border-radius": string;
  padding: string;
}

export interface AMapPolyline {
  setOptions(options: { strokeColor: string }): void;
}

export interface AMapText {
  setStyle(style: AMapTextStyle): void;
}

export interface AMapMap {
  add(overlay: unknown): void;
  setFitView(): void;
  setMapStyle(style: string): void;
}

export interface AMapNamespace {
  Map: new (container: string, options: object) => AMapMap;
  Polyline: new (options: object) => AMapPolyline;
  Text: new (options: object) => AMapText;
  Marker: new (options: object) => unknown;
}

export interface MapSegment {
  kind: string;
  dashed: boolean;
  path: number[][];
  label: string;
}

export function mapShown(): boolean {
  return document.documentElement.getAttribute("data-map") !== "off";
}

export function amapStyle(): string {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "amap://styles/dark"
    : "amap://styles/normal";
}

export function cssColor(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function dark(): boolean {
  return document.documentElement.getAttribute("data-theme") === "dark";
}

export function labelStyle(): AMapTextStyle {
  return {
    color: dark() ? "#f5f5f7" : "#1c1c1e",
    background: dark() ? "rgba(28,28,30,0.92)" : "rgba(255,255,255,0.92)",
    border: "0",
    "font-size": "12px",
    "border-radius": "6px",
    padding: "2px 6px",
  };
}

export function lineColor(dashed: boolean): string {
  return dashed ? cssColor("--line") : cssColor("--accent");
}

export function readJson(id: string): unknown {
  const node = document.getElementById(id);
  if (!node || !node.textContent) return null;
  return JSON.parse(node.textContent);
}

let loading: Promise<void> | null = null;

export function loadAmap(src: string): Promise<void> {
  if (window.AMap) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => resolve();
    document.head.appendChild(script);
  });
  return loading;
}

export function resetAmapLoader(): void {
  loading = null;
}
