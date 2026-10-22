import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetAmapLoader, type AMapNamespace } from "../amap";
import ChargeMap from "./ChargeMap.vue";
import DriveMap from "./DriveMap.vue";

const AMAP_SRC = "https://webapi.amap.com/maps?v=2.0&key=test";

interface PolylineFake {
  setOptions: ReturnType<typeof vi.fn>;
  options: { strokeWeight: number; strokeStyle: string };
}

interface TextFake {
  setStyle: ReturnType<typeof vi.fn>;
  options: { text: string };
}

interface MarkerFake {
  options: { position: number[] };
}

function installAmap() {
  const polylines: PolylineFake[] = [];
  const texts: TextFake[] = [];
  const markers: MarkerFake[] = [];
  const map = {
    add: vi.fn(),
    setFitView: vi.fn(),
    setMapStyle: vi.fn(),
  };
  const AMap = {
    Map: vi.fn(() => map),
    Polyline: vi.fn((options: PolylineFake["options"]) => {
      const line = { options, setOptions: vi.fn() };
      polylines.push(line);
      return line;
    }),
    Text: vi.fn((options: TextFake["options"]) => {
      const label = { options, setStyle: vi.fn() };
      texts.push(label);
      return label;
    }),
    Marker: vi.fn((options: MarkerFake["options"]) => {
      const marker = { options };
      markers.push(marker);
      return marker;
    }),
  };
  return { AMap: AMap as unknown as AMapNamespace, map, polylines, texts, markers };
}

function amapScript(): HTMLScriptElement | null {
  return [...document.scripts].find((script) => script.src.includes("webapi.amap.com")) ?? null;
}

async function finishLoad(fake = installAmap()): Promise<ReturnType<typeof installAmap>> {
  await nextTick();
  const script = amapScript();
  if (!script) throw new Error("高德脚本没有插入");
  window.AMap = fake.AMap;
  script.onload?.(new Event("load"));
  await flushPromises();
  return fake;
}

beforeEach(() => {
  document.body.innerHTML = "";
  document.head.querySelectorAll('script[src*="webapi.amap.com"]').forEach((node) => node.remove());
  document.documentElement.removeAttribute("data-map");
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.setProperty("--accent", "#007aff");
  document.documentElement.style.setProperty("--line", "#8e8e93");
  delete window.AMap;
  resetAmapLoader();
});

describe("DriveMap", () => {
  const segments = [
    { kind: "drive", dashed: false, path: [[121.5, 31.2], [121.6, 31.3]], label: "" },
    { kind: "park", dashed: true, path: [], label: "停车" },
    { kind: "park", dashed: true, path: [[121.6, 31.3], [121.7, 31.4]], label: "停车" },
  ];

  function placeData(): void {
    document.body.innerHTML = `<script id="map-data" type="application/json">${JSON.stringify(segments)}</script>`;
  }

  it("draws solid drives and dashed parking labels", async () => {
    placeData();
    mount(DriveMap, { props: { amapSrc: AMAP_SRC } });
    const fake = await finishLoad();

    expect(fake.polylines).toHaveLength(2);
    expect(fake.polylines[0].options).toMatchObject({ strokeWeight: 5, strokeStyle: "solid" });
    expect(fake.polylines[1].options).toMatchObject({ strokeWeight: 3, strokeStyle: "dashed" });
    expect(fake.texts[0].options.text).toBe("停车");
    expect(fake.map.setFitView).toHaveBeenCalledOnce();
    expect(fake.map.add).toHaveBeenCalledOnce();
  });

  it("does not load AMap while the map is hidden, then restyles after the theme event", async () => {
    placeData();
    document.documentElement.setAttribute("data-map", "off");
    mount(DriveMap, { props: { amapSrc: AMAP_SRC } });
    await nextTick();
    expect(amapScript()).toBeNull();

    document.documentElement.removeAttribute("data-map");
    document.dispatchEvent(new Event("teslaslip-theme"));
    const fake = await finishLoad();

    document.documentElement.setAttribute("data-theme", "dark");
    document.dispatchEvent(new Event("teslaslip-theme"));
    expect(fake.map.setMapStyle).toHaveBeenCalledWith("amap://styles/dark");
    expect(fake.polylines[0].setOptions).toHaveBeenCalledWith({ strokeColor: "#007aff" });
    expect(fake.polylines[1].setOptions).toHaveBeenCalledWith({ strokeColor: "#8e8e93" });
    expect(fake.texts[0].setStyle).toHaveBeenCalledWith(expect.objectContaining({ color: "#f5f5f7" }));
  });
});

describe("ChargeMap", () => {
  it("places a marker on the charge point", async () => {
    document.body.innerHTML = '<script id="charge-point" type="application/json">{"path":[121.5,31.2]}</script>';
    mount(ChargeMap, { props: { amapSrc: AMAP_SRC } });
    const fake = await finishLoad();

    expect(fake.AMap.Map).toHaveBeenCalledWith("map", expect.objectContaining({ zoom: 15, center: [121.5, 31.2] }));
    expect(fake.markers[0].options.position).toEqual([121.5, 31.2]);
    expect(fake.map.add).toHaveBeenCalledOnce();
  });

  it("skips an empty point and a hidden map", async () => {
    document.body.innerHTML = '<script id="charge-point" type="application/json">null</script>';
    mount(ChargeMap, { props: { amapSrc: AMAP_SRC } });
    const fake = await finishLoad();
    expect(fake.AMap.Map).not.toHaveBeenCalled();

    resetAmapLoader();
    delete window.AMap;
    document.head.querySelectorAll('script[src*="webapi.amap.com"]').forEach((node) => node.remove());
    document.documentElement.setAttribute("data-map", "off");
    mount(ChargeMap, { props: { amapSrc: AMAP_SRC } });
    await nextTick();
    expect(amapScript()).toBeNull();
  });
});
