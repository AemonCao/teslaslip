import { createApp } from "vue";

import type { ChartData } from "../charts";
import ChartsApp from "../components/ChartsApp.vue";

const element = document.getElementById("charts-app");
const source = document.getElementById("chart-data");
if (element && source) {
  try {
    const data = JSON.parse(source.textContent ?? "") as ChartData;
    if (!data.metrics || !data.segments || !Number.isFinite(data.start) || !Number.isFinite(data.end)) throw new Error("Invalid chart data");
    const app = createApp(ChartsApp, { data });
    app.config.errorHandler = () => {
      element.textContent = "图表暂时无法显示，文字总结可继续阅读。";
    };
    app.mount(element);
  } catch {
    element.textContent = "图表暂时无法显示，文字总结可继续阅读。";
  }
}
