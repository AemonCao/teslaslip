<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

import { amapStyle, loadAmap, mapShown, readJson, type AMapMap } from "../amap";

const props = defineProps<{ amapSrc: string }>();

let map: AMapMap | null = null;
let started = false;

function restyle(): void {
  if (!map) return;
  map.setMapStyle(amapStyle());
}

function boot(): void {
  const amap = window.AMap;
  if (!amap || map) return;
  const point = readJson("charge-point");
  if (!point || typeof point !== "object" || !("path" in point)) return;
  const path = point.path;
  if (!Array.isArray(path)) return;
  map = new amap.Map("map", { viewMode: "2D", zoom: 15, center: path, mapStyle: amapStyle() });
  map.add(new amap.Marker({ position: path }));
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
