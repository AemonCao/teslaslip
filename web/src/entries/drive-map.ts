import { createApp } from "vue";

import DriveMap from "../components/DriveMap.vue";

const el = document.getElementById("drive-map-app");
if (el) createApp(DriveMap, { amapSrc: el.getAttribute("data-amap-src") ?? "" }).mount(el);
