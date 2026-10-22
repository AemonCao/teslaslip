import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { install } from "../prefs";
import SettingsApp from "./SettingsApp.vue";

const labels = [
  "跟随系统",
  "浅色",
  "深色",
  "标准",
  "大",
  "显示地图",
  "紧凑",
  "高",
  "驾驶",
  "续航",
  "里程表",
  "环境",
  "车辆",
  "分段",
  "导航目的地",
  "额定",
  "理想",
  "都显示",
  "采样明细",
  "恢复默认",
];

beforeEach(() => {
  localStorage.clear();
  document.body.innerHTML = '<div id="settings-back-slot"></div><div id="app"></div>';
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-map");
  document.documentElement.removeAttribute("data-hide");
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
  install();
});

function mountApp() {
  return mount(SettingsApp, { attachTo: document.getElementById("app")! });
}

describe("SettingsApp", () => {
  it("lists the display controls and returns through history", async () => {
    const back = vi.spyOn(history, "back").mockImplementation(() => {});
    const wrapper = mountApp();
    for (const label of labels) expect(wrapper.text()).toContain(label);

    document.querySelector("#settings-back-slot button")?.dispatchEvent(new MouseEvent("click"));
    expect(back).toHaveBeenCalledOnce();
    wrapper.unmount();
  });

  it("saves theme, map, section, and reset choices", async () => {
    const wrapper = mountApp();
    await wrapper.get('[aria-label="主题"] button:last-child').trigger("click");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(JSON.parse(localStorage.getItem("teslaslip-ui") ?? "").theme).toBe("dark");

    const mapRow = wrapper.findAll(".row").find((row) => row.text().startsWith("显示地图"));
    await mapRow!.get("button").trigger("click");
    expect(document.documentElement.getAttribute("data-map")).toBe("off");
    expect(mapRow!.get("button").text()).toBe("关");

    const driving = wrapper.findAll(".row").find((row) => row.text().startsWith("驾驶"));
    await driving!.get("button").trigger("click");
    expect(document.documentElement.getAttribute("data-hide")).toContain("driving");

    await wrapper.get("button.reset").trigger("click");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(document.documentElement.hasAttribute("data-map")).toBe(false);
    expect(document.documentElement.hasAttribute("data-hide")).toBe(false);
    expect(mapRow!.get("button").text()).toBe("开");
    wrapper.unmount();
  });
});
