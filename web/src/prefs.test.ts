import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UI_KEY, defaults, install, normalize, read, save } from "./prefs";

function clearRoot(): void {
  const root = document.documentElement;
  for (const name of ["data-theme", "data-accent", "data-text", "data-map", "data-map-height", "data-range", "data-hide"]) {
    root.removeAttribute(name);
  }
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  clearRoot();
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: () => false,
    onchange: null,
  })) as typeof window.matchMedia;
});

describe("normalize", () => {
  it("drops unknown values and keeps valid section flags", () => {
    expect(
      normalize({
        theme: "blue",
        accent: "nope",
        text: "huge",
        map: "yes",
        mapHeight: "huge",
        rangeMode: "nope",
        chargeSamples: 1,
        sections: { driving: "no", range: false },
      }),
    ).toEqual({
      ...defaults(),
      sections: { ...defaults().sections, range: false },
    });
  });
});

describe("read", () => {
  it("uses the legacy theme key only when the new key is absent", () => {
    localStorage.setItem("teslaslip-theme", "dark");
    expect(read().theme).toBe("dark");

    localStorage.setItem(UI_KEY, JSON.stringify({ ...defaults(), theme: "light" }));
    expect(read().theme).toBe("light");
  });

  it("returns defaults when stored json is invalid", () => {
    localStorage.setItem(UI_KEY, "not-json");
    localStorage.setItem("teslaslip-theme", "dark");
    expect(read()).toEqual(defaults());
  });
});

describe("save", () => {
  it("writes attributes for accent, text, map, range, and hidden sections", () => {
    const seen = vi.fn();
    document.addEventListener("teslaslip-theme", seen);
    save({
      ...defaults(),
      theme: "dark",
      accent: "green",
      text: "large",
      map: false,
      mapHeight: "tall",
      rangeMode: "rated",
      chargeSamples: false,
      sections: { ...defaults().sections, driving: false, vehicle: false },
    });

    const root = document.documentElement;
    expect(root.getAttribute("data-theme")).toBe("dark");
    expect(root.getAttribute("data-accent")).toBe("green");
    expect(root.getAttribute("data-text")).toBe("large");
    expect(root.getAttribute("data-map")).toBe("off");
    expect(root.getAttribute("data-map-height")).toBe("tall");
    expect(root.getAttribute("data-range")).toBe("rated");
    expect(root.getAttribute("data-hide")).toBe("driving vehicle samples");
    expect(JSON.parse(localStorage.getItem(UI_KEY) ?? "")).toMatchObject({ accent: "green", map: false });
    expect(seen).toHaveBeenCalled();
    document.removeEventListener("teslaslip-theme", seen);
  });

  it("clears attributes that mean the default", () => {
    save({ ...defaults(), accent: "green", text: "large", map: false, theme: "dark" });
    save(defaults());
    const root = document.documentElement;
    expect(root.hasAttribute("data-theme")).toBe(false);
    expect(root.hasAttribute("data-accent")).toBe(false);
    expect(root.hasAttribute("data-text")).toBe(false);
    expect(root.hasAttribute("data-map")).toBe(false);
    expect(root.hasAttribute("data-hide")).toBe(false);
  });

  it("still applies the choice when storage rejects the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    expect(save({ ...defaults(), theme: "dark" }).theme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});

describe("install", () => {
  it("reapplies stored prefs on pageshow and watches the system scheme", () => {
    const queries: string[] = [];
    let matches = false;
    let listener: (() => void) | undefined;
    window.matchMedia = vi.fn().mockImplementation((query: string) => {
      queries.push(query);
      return {
        get matches() {
          return matches;
        },
        media: query,
        addEventListener: (_type: string, fn: () => void) => {
          listener = fn;
        },
        removeEventListener: vi.fn(),
        addListener: (fn: () => void) => {
          listener = fn;
        },
        removeListener: vi.fn(),
        dispatchEvent: () => false,
        onchange: null,
      };
    }) as typeof window.matchMedia;

    install();
    expect(queries).toContain("(prefers-color-scheme: dark)");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);

    matches = true;
    listener?.();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    save({ ...read(), theme: "light" });
    listener?.();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);

    localStorage.setItem(UI_KEY, JSON.stringify({ ...defaults(), theme: "dark" }));
    window.dispatchEvent(new Event("pageshow"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.teslaslipUI.read).toBe(read);
  });
});
