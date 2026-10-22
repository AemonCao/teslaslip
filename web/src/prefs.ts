export const UI_KEY = "teslaslip-ui";
export const LEGACY_KEY = "teslaslip-theme";

const ACCENTS = ["blue", "green", "orange", "purple"] as const;
const HEIGHTS = ["compact", "standard", "tall"] as const;
const RANGES = ["rated", "ideal", "both"] as const;
export const SECTION_NAMES = [
  "driving",
  "range",
  "odometer",
  "environment",
  "vehicle",
  "segments",
  "destination",
] as const;

export type Accent = (typeof ACCENTS)[number];
export type MapHeight = (typeof HEIGHTS)[number];
export type RangeMode = (typeof RANGES)[number];
export type SectionName = (typeof SECTION_NAMES)[number];
export type ThemeName = "system" | "light" | "dark";
export type TextSize = "standard" | "large";

export interface SectionPrefs {
  driving: boolean;
  range: boolean;
  odometer: boolean;
  environment: boolean;
  vehicle: boolean;
  segments: boolean;
  destination: boolean;
}

export interface Prefs {
  theme: ThemeName;
  accent: Accent;
  text: TextSize;
  map: boolean;
  mapHeight: MapHeight;
  sections: SectionPrefs;
  rangeMode: RangeMode;
  chargeSamples: boolean;
}

export interface TeslaSlipUI {
  defaults: () => Prefs;
  read: () => Prefs;
  save: (prefs: Prefs) => Prefs;
}

function includes<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

export function defaults(): Prefs {
  return {
    theme: "system",
    accent: "blue",
    text: "standard",
    map: true,
    mapHeight: "standard",
    sections: {
      driving: true,
      range: true,
      odometer: true,
      environment: true,
      vehicle: true,
      segments: true,
      destination: true,
    },
    rangeMode: "both",
    chargeSamples: true,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function normalize(raw: unknown): Prefs {
  const base = defaults();
  if (!isRecord(raw)) return base;
  if (raw.theme === "light" || raw.theme === "dark" || raw.theme === "system") base.theme = raw.theme;
  if (includes(ACCENTS, raw.accent)) base.accent = raw.accent;
  if (raw.text === "large" || raw.text === "standard") base.text = raw.text;
  if (typeof raw.map === "boolean") base.map = raw.map;
  if (includes(HEIGHTS, raw.mapHeight)) base.mapHeight = raw.mapHeight;
  if (includes(RANGES, raw.rangeMode)) base.rangeMode = raw.rangeMode;
  if (typeof raw.chargeSamples === "boolean") base.chargeSamples = raw.chargeSamples;
  if (isRecord(raw.sections)) {
    for (const name of SECTION_NAMES) {
      if (typeof raw.sections[name] === "boolean") base.sections[name] = raw.sections[name];
    }
  }
  return base;
}

export function read(): Prefs {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (raw) return normalize(JSON.parse(raw));
    const prefs = defaults();
    const legacy = localStorage.getItem(LEGACY_KEY);
    if (legacy === "light" || legacy === "dark") prefs.theme = legacy;
    return prefs;
  } catch {
    return defaults();
  }
}

function systemDark(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

function resolvedDark(prefs: Prefs): boolean {
  if (prefs.theme === "dark") return true;
  if (prefs.theme === "light") return false;
  return systemDark();
}

export function apply(prefs: Prefs): void {
  const root = document.documentElement;
  if (resolvedDark(prefs)) root.setAttribute("data-theme", "dark");
  else root.removeAttribute("data-theme");
  if (prefs.accent === "blue") root.removeAttribute("data-accent");
  else root.setAttribute("data-accent", prefs.accent);
  if (prefs.text === "large") root.setAttribute("data-text", "large");
  else root.removeAttribute("data-text");
  if (prefs.map) root.removeAttribute("data-map");
  else root.setAttribute("data-map", "off");
  if (prefs.mapHeight === "standard") root.removeAttribute("data-map-height");
  else root.setAttribute("data-map-height", prefs.mapHeight);
  if (prefs.rangeMode === "both") root.removeAttribute("data-range");
  else root.setAttribute("data-range", prefs.rangeMode);
  const hidden: string[] = [];
  for (const name of SECTION_NAMES) {
    if (!prefs.sections[name]) hidden.push(name);
  }
  if (!prefs.chargeSamples) hidden.push("samples");
  if (hidden.length) root.setAttribute("data-hide", hidden.join(" "));
  else root.removeAttribute("data-hide");
  document.dispatchEvent(new Event("teslaslip-theme"));
}

export function save(prefs: Prefs): Prefs {
  const next = normalize(prefs);
  try {
    localStorage.setItem(UI_KEY, JSON.stringify(next));
  } catch {
    // 无痕模式或存储满了时，这一页仍然套用刚选出的值。
  }
  apply(next);
  return next;
}

type LegacyMediaQueryList = MediaQueryList & {
  addListener?: (listener: () => void) => void;
};

export function install(): void {
  apply(read());
  window.addEventListener("pageshow", () => {
    apply(read());
  });
  try {
    const media = window.matchMedia("(prefers-color-scheme: dark)") as LegacyMediaQueryList;
    const onScheme = () => {
      const prefs = read();
      if (prefs.theme === "system") apply(prefs);
    };
    if (typeof media.addEventListener === "function") media.addEventListener("change", onScheme);
    else media.addListener?.(onScheme);
  } catch {
    // 少数 WebView 没有 matchMedia。
  }
  window.teslaslipUI = { defaults, read, save };
}
