<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { dateText, durationText, timeText, type ChartData, type Metric } from "../charts";
import { read } from "../prefs";
import TimeChart from "./TimeChart.vue";

const props = defineProps<{ data: ChartData }>();
const cursor = ref<number | null>(null);
const expanded = ref(false);
const prefs = ref(read());
const enabled = computed(() => props.data.kind === "drive" ? prefs.value.driveCharts : prefs.value.chargeCharts);
const groups = computed(() => {
  const result = new Map<string, Metric[]>();
  for (const metric of props.data.metrics) {
    if (metric.extra && !metric.count) continue;
    if (metric.rangeMode && prefs.value.rangeMode !== "both" && prefs.value.rangeMode !== metric.rangeMode) continue;
    const group = result.get(metric.group) ?? [];
    group.push(metric);
    result.set(metric.group, group);
  }
  return [...result.values()];
});
const core = computed(() => groups.value.filter((group) => !group[0].extra));
const extra = computed(() => groups.value.filter((group) => group[0].extra));
function updatePrefs(): void { prefs.value = read(); }
function toggleDetails(event: Event): void { expanded.value = (event.target as HTMLDetailsElement).open; }
onMounted(() => document.addEventListener("teslaslip-theme", updatePrefs));
onBeforeUnmount(() => document.removeEventListener("teslaslip-theme", updatePrefs));
</script>

<template>
  <div v-if="enabled" class="charts">
    <p class="chart-intro">{{ dateText(data.start) }}{{ dateText(data.end) !== dateText(data.start) ? ` ～ ${dateText(data.end)}` : '' }} · 上海时间（UTC+8）<br>点按／横向拖动查看，纵向滑动页面；键盘方向键逐点查看。长序列按时间桶保留首尾与峰谷，提示为最近保留的真实采样，未插值。</p>
    <TimeChart v-for="group in core" :key="group[0].group" :metrics="group" :data="data" :cursor="cursor" @cursor="cursor = $event" />
    <details v-if="extra.length" class="chart-details" @toggle="toggleDetails">
      <summary>更多环境与车辆图表（{{ extra.length }}）</summary>
      <template v-if="expanded">
        <TimeChart v-for="group in extra" :key="group[0].group" :metrics="group" :data="data" :cursor="cursor" @cursor="cursor = $event" />
      </template>
    </details>
    <article v-if="data.distribution" class="card speed-distribution">
      <h3>速度时长分布</h3>
      <p class="note">有有效速度采样覆盖的驾驶时间 {{ durationText(data.distribution.coveredSeconds) }}；未覆盖 {{ durationText(data.distribution.uncoveredSeconds) }}。按相邻采样间隔及前值累计，不计长缺口及跨段停车。</p>
      <div v-for="bin in data.distribution.bins" :key="bin.label" class="distribution-row">
        <div><span>{{ bin.label }} km/h</span><span>{{ durationText(bin.seconds) }} · {{ bin.percent.toFixed(1) }}%</span></div>
        <div class="distribution-track" role="meter" :aria-label="`${bin.label} km/h`" :aria-valuenow="bin.percent" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${bin.percent}%` }"></span></div>
      </div>
      <h3>段内静止等待 ≥30 秒</h3>
      <p v-if="!data.distribution.waiting.length" class="note">暂无符合条件的连续零速区间。</p>
      <p v-for="(wait, index) in data.distribution.waiting" :key="index" class="chart-wait">第 {{ wait.segment }} 段 · {{ timeText(wait.start) }}–{{ timeText(wait.end) }} · {{ durationText(wait.seconds) }}</p>
      <p class="note">仅表示驾驶段内有连续零速证据，不自动解释为红灯或堵车。</p>
    </article>
  </div>
</template>
