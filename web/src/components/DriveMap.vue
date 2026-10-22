<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

import {
  amapStyle,
  labelStyle,
  lineColor,
  loadAmap,
  mapShown,
  readJson,
  type AMapMap,
  type AMapPolyline,
  type AMapText,
  type MapSegment,
} from "../amap";

const props = defineProps<{ amapSrc: string }>();

const lines: { line: AMapPolyline; dashed: boolean }[] = [];
const labels: AMapText[] = [];
let map: AMapMap | null = null;
let started = false;

function restyle(): void {
  if (!map) return;
  map.setMapStyle(amapStyle());
  const style = labelStyle();
  for (const label of labels) label.setStyle(style);
  for (const item of lines) item.line.setOptions({ strokeColor: lineColor(item.dashed) });
}

function boot(): void {
  const amap = window.AMap;
  if (!amap || map) return;
  const segments = readJson("map-data");
  if (!Array.isArray(segments)) return;
  map = new amap.Map("map", { viewMode: "2D", mapStyle: amapStyle() });
  const overlays: unknown[] = [];
  for (const segment of segments as MapSegment[]) {
    if (!segment.path.length) continue;
    const line = new amap.Polyline({
      path: segment.path,
      strokeColor: lineColor(segment.dashed),
      strokeOpacity: 1,
      strokeWeight: segment.dashed ? 3 : 5,
      strokeStyle: segment.dashed ? "dashed" : "solid",
    });
    lines.push({ line, dashed: segment.dashed });
    overlays.push(line);
    if (segment.dashed && segment.label) {
      const first = segment.path[0];
      const last = segment.path[segment.path.length - 1];
      const mid = [(first[0] + last[0]) / 2, (first[1] + last[1]) / 2];
      const label = new amap.Text({ text: segment.label, position: mid, style: labelStyle() });
      labels.push(label);
      overlays.push(label);
    }
  }
  map.add(overlays);
  if (overlays.length) map.setFitView();
}

function start(): void {
  if (started || !mapShown() || !props.amapSrc) return;
  started = true;
  void loadAmap(props.amapSrc).then(() => {
    boot();
  });
}

function onTheme(): void {
  if (!started) start();
  else restyle();
}

onMounted(() => {
  start();
  document.addEventListener("teslaslip-theme", onTheme);
});

onBeforeUnmount(() => {
  document.removeEventListener("teslaslip-theme", onTheme);
});
</script>

<template>
  <div id="map"></div>
</template>
