import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { linePath, nearest, timeText, valueText, yDomain, type ChartData, type Metric } from "../charts";
import { defaults, normalize, save } from "../prefs";
import ChartsApp from "./ChartsApp.vue";
import TimeChart from "./TimeChart.vue";

const start = Date.parse("2026-10-31T02:00:00Z");
function metric(overrides: Partial<Metric> = {}): Metric {
  return {
    key: "speed", name: "速度", unit: "km/h", group: "speed", style: "line", extra: false,
    note: "", rangeMode: null, count: 3, min: 0, max: 40, anomalies: [],
    chunks: [{ segment: 1, threshold: 30000, points: [[start, 0], [start + 10000, 40], [start + 20000, 0]] }],
    ...overrides,
  };
}
function data(metrics: Metric[] = [metric()]): ChartData {
  return { kind: "drive", start, end: start + 100000, segments: [{ index: 1, start, end: start + 40000 }, { index: 2, start: start + 60000, end: start + 100000 }], metrics, distribution: null };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-map");
  document.documentElement.removeAttribute("data-hide");
});

describe("chart data helpers", () => {
  it("keeps zeros and negative signs, uses Shanghai time, and never extrapolates into gaps", () => {
    const series = metric({ chunks: [
      { segment: 1, threshold: 30000, points: [[start, 0], [start + 10000, 40]] },
      { segment: 2, threshold: 30000, points: [[start + 60000, 10]] },
    ] });
    expect(timeText(start)).toBe("10:00:00");
    expect(nearest(series, start + 1000)?.point[1]).toBe(0);
    expect(nearest(series, start + 50000)).toBeNull();
    expect(nearest(series, start - 1)).toBeNull();
    expect(nearest(series, start + 60000)?.segment).toBe(2);
    expect(valueText(metric({ style: "power", unit: "kW" }), -10)).toBe("-10 kW");
    expect(valueText(metric({ style: "power", unit: "kW" }), 10)).toBe("+10 kW");
    expect(valueText(metric({ style: "step", unit: "%" }), 80)).toBe("80 %");
    expect(linePath([[0, 1], [2, 3]], value => value, value => value, true)).toBe("M0.00,1.00 H2.00V3.00");
    expect(yDomain([metric({ min: 10, max: 10 })]).every(Number.isFinite)).toBe(true);
  });
});

describe("TimeChart", () => {
  it("renders raw peak, independent axes, singletons, empty data, and a real parking gap", () => {
    const speed = metric({ chunks: [
      { segment: 1, threshold: 30000, points: [[start, 0], [start + 10000, 40]] },
      { segment: 2, threshold: 30000, points: [[start + 60000, 10]] },
    ] });
    const wrapper = mount(TimeChart, { props: { metrics: [speed], data: data([speed]), cursor: start + 50000 } });
    expect(wrapper.text()).toContain("采样最高 40 km/h");
    expect(wrapper.findAll(".chart-line")).toHaveLength(2);
    expect(wrapper.findAll(".chart-point")).toHaveLength(3);
    expect(Number(wrapper.find(".chart-parking").attributes("width"))).toBeCloseTo(109.2);
    expect(wrapper.text()).toContain("停车");
    expect(wrapper.text()).toContain("无采样");
    expect(wrapper.html()).not.toMatch(/NaN|Infinity/);
    wrapper.unmount();
    const empty = metric({ chunks: [], count: 0, min: null, max: null });
    const blank = mount(TimeChart, { props: { metrics: [empty], data: data([empty]), cursor: null } });
    expect(blank.text()).toContain("暂无采样数据");
    expect(blank.find("svg").exists()).toBe(false);
    blank.unmount();
  });

  it("renders clipped positive and negative areas, step geometry, resets, and time differences", () => {
    const power = metric({ key: "power", name: "输出／回收功率", unit: "kW", group: "power", style: "power", min: -10, max: 20, chunks: [{ segment: 1, threshold: 30000, points: [[start, -10], [start + 10000, 20]] }] });
    const wrapper = mount(TimeChart, { props: { metrics: [power], data: data([power]), cursor: start + 9000 } });
    expect(wrapper.find(".chart-zero").exists()).toBe(true);
    expect(wrapper.get(".chart-positive").attributes("clip-path")).toContain("positive-power");
    expect(wrapper.find(".chart-negative").exists()).toBe(true);
    expect(wrapper.text()).toContain("+20 kW");
    expect(wrapper.text()).toContain("相差 1 秒");
    wrapper.unmount();
    const battery = metric({ key: "battery_level", name: "电量", unit: "%", style: "step", anomalies: [start + 10000] });
    const step = mount(TimeChart, { props: { metrics: [battery], data: data([battery]), cursor: null } });
    expect(step.get(".chart-line").attributes("d")).toMatch(/H.*V/);
    expect(step.text()).toContain("数据异常");
    step.unmount();
  });

  it("supports keyboard sample navigation, horizontal drag, and vertical scroll cancellation", async () => {
    const wrapper = mount(TimeChart, { props: { metrics: [metric()], data: data(), cursor: null } });
    const svg = wrapper.get("svg");
    await svg.trigger("keydown", { key: "ArrowRight" });
    expect(wrapper.emitted("cursor")?.at(-1)).toEqual([start]);
    await wrapper.setProps({ cursor: start });
    await svg.trigger("keydown", { key: "ArrowRight" });
    expect(wrapper.emitted("cursor")?.at(-1)).toEqual([start + 10000]);
    await svg.trigger("keydown", { key: "End" });
    expect(wrapper.emitted("cursor")?.at(-1)).toEqual([start + 20000]);
    await svg.trigger("keydown", { key: "Home" });
    expect(wrapper.emitted("cursor")?.at(-1)).toEqual([start]);
    vi.spyOn(svg.element, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, width: 640, height: 220, right: 640, bottom: 220, toJSON: () => ({}) });
    function pointer(type: string, id: number, clientX: number, clientY: number): void {
      const event = new MouseEvent(type, { clientX, clientY, bubbles: true });
      Object.defineProperties(event, { pointerId: { value: id }, pointerType: { value: "touch" } });
      svg.element.dispatchEvent(event);
    }
    pointer("pointerdown", 1, 76, 100);
    pointer("pointermove", 1, 349, 102);
    expect(wrapper.emitted("cursor")?.at(-1)).toEqual([start + 50000]);
    await svg.trigger("pointercancel");
    pointer("pointerdown", 2, 76, 100);
    const count = wrapper.emitted("cursor")?.length;
    pointer("pointermove", 2, 78, 150);
    expect(wrapper.emitted("cursor")).toHaveLength(count!);
    wrapper.unmount();
  });
});

describe("ChartsApp", () => {
  it("links cursors, lazily mounts nonempty auxiliary charts, and respects display and range preferences", async () => {
    const speed = metric();
    const power = metric({ key: "power", name: "输出／回收功率", group: "power", unit: "kW", style: "power" });
    const temperature = metric({ key: "inside_temp", name: "车内温度", group: "temperature", unit: "°C", extra: true });
    const missing = metric({ key: "tpms_fl", group: "tires", extra: true, count: 0, chunks: [] });
    const range = metric({ key: "rated_range", group: "range", rangeMode: "rated", extra: true });
    const wrapper = mount(ChartsApp, { props: { data: data([speed, power, temperature, missing, range]) } });
    expect(wrapper.findAllComponents(TimeChart)).toHaveLength(2);
    await wrapper.findComponent(TimeChart).get("svg").trigger("keydown", { key: "Home" });
    expect(wrapper.findAllComponents(TimeChart).every(chart => chart.props("cursor") === start)).toBe(true);
    (wrapper.get("details").element as HTMLDetailsElement).open = true;
    await wrapper.get("details").trigger("toggle");
    expect(wrapper.findAllComponents(TimeChart)).toHaveLength(4);
    save({ ...defaults(), rangeMode: "ideal", map: false, theme: "dark" });
    await wrapper.vm.$nextTick();
    expect(wrapper.findAllComponents(TimeChart)).toHaveLength(3);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(wrapper.text()).toContain("速度");
    save({ ...defaults(), driveCharts: false });
    await wrapper.vm.$nextTick();
    expect(wrapper.findAllComponents(TimeChart)).toHaveLength(0);
    expect(document.documentElement.getAttribute("data-hide")).toContain("driveCharts");
    wrapper.unmount();
  });

  it("toggles same-unit series, preserves charge preference, and defaults old preferences on", async () => {
    expect(normalize({ map: false })).toMatchObject({ driveCharts: true, chargeCharts: true });
    const temperature = metric({ name: "车内温度", group: "temperature" });
    const outside = metric({ key: "outside_temp", name: "车外温度", group: "temperature" });
    const wrapper = mount(TimeChart, { props: { metrics: [temperature, outside], data: data(), cursor: null } });
    expect(wrapper.findAll(".chart-line")).toHaveLength(2);
    await wrapper.findAll("button")[0].trigger("click");
    expect(wrapper.findAll(".chart-line")).toHaveLength(1);
    await wrapper.findAll("button")[1].trigger("click");
    expect(wrapper.text()).toContain("请开启一项指标");
    wrapper.unmount();
    save({ ...defaults(), chargeCharts: false });
    const charge = mount(ChartsApp, { props: { data: { ...data(), kind: "charge" } } });
    expect(charge.findAllComponents(TimeChart)).toHaveLength(0);
    charge.unmount();
  });

  it("shows the time-weighted distribution denominator, uncovered time, and waiting evidence", () => {
    const sample = data();
    sample.distribution = { bins: [{ label: "静止 0", seconds: 30, percent: 100 }], coveredSeconds: 30, uncoveredSeconds: 50, waiting: [{ segment: 1, start, end: start + 30000, seconds: 30 }] };
    const wrapper = mount(ChartsApp, { props: { data: sample } });
    expect(wrapper.text()).toContain("未覆盖 50 秒");
    expect(wrapper.text()).toContain("10:00:00–10:00:30");
    expect(wrapper.get('[role="meter"]').attributes("aria-valuenow")).toBe("100");
    wrapper.unmount();
  });
});
