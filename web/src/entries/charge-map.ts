import { createApp } from "vue";

import ChargeMap from "../components/ChargeMap.vue";

const el = document.getElementById("charge-map-app");
if (el) createApp(ChargeMap, { amapSrc: el.getAttribute("data-amap-src") ?? "" }).mount(el);
