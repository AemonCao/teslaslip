<script setup lang="ts">
import { computed, ref } from "vue";

import { linePath, nearest, timeText, valueText, yDomain, type ChartData, type Metric, type Point } from "../charts";

const props = defineProps<{ metrics: Metric[]; data: ChartData; cursor: number | null }>();
const emit = defineEmits<{ cursor: [stamp: number] }>();
const hidden = ref<string[]>([]);
const active = computed(() => props.metrics.filter((metric) => !hidden.value.includes(metric.key)));
const domain = computed(() => yDomain(active.value));
const first = computed(() => props.metrics[0]);
const span = computed(() => Math.max(1, props.data.end - props.data.start));
const x = (stamp: number) => 76 + (stamp - props.data.start) / span.value * 546;
const y = (value: number) => 18 + (domain.value[1] - value) / (domain.value[1] - domain.value[0]) * 164;
const ticks = computed(() => [0, 0.5, 1].map((fraction) => props.data.start + fraction * (props.data.end - props.data.start)));
const readings = computed(() => active.value.map((metric) => ({ metric, reading: props.cursor === null ? null : nearest(metric, props.cursor) })));
const sampleTimes = computed(() => [...new Set(active.value.flatMap((metric) => metric.chunks.flatMap((chunk) => chunk.points.map((point) => point[0]))))].sort((left, right) => left - right));
const caption = computed(() => props.metrics.map((metric) => metric.name).join("／"));
const positionText = computed(() => props.cursor === null ? "左右方向键查看采样" : `${timeText(props.cursor)}，${readings.value.map(({ metric, reading }) => `${metric.name} ${reading ? valueText(metric, reading.point[1]) : '无采样'}`).join('，')}`);

function toggle(key: string): void {
  hidden.value = hidden.value.includes(key) ? hidden.value.filter((item) => item !== key) : [...hidden.value, key];
}

function path(metric: Metric, points: Point[]): string {
  return linePath(points, x, y, metric.style === "step" || metric.style === "state");
}

function area(metric: Metric, points: Point[]): string {
  return `${path(metric, points)} L${x(points[points.length - 1][0])},${y(0)} L${x(points[0][0])},${y(0)} Z`;
}

function select(event: PointerEvent): void {
  const bounds = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
  if (!bounds.width) return;
  const fraction = Math.max(0, Math.min(1, ((event.clientX - bounds.left) / bounds.width * 640 - 76) / 546));
  emit("cursor", Math.round(props.data.start + fraction * span.value));
}

let pointer: { id: number; x: number; y: number; horizontal: boolean } | null = null;
function down(event: PointerEvent): void {
  pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, horizontal: false };
  select(event);
}
function move(event: PointerEvent): void {
  if (event.pointerType === "mouse" && !pointer) { select(event); return; }
  if (!pointer || pointer.id !== event.pointerId) return;
  const deltaX = Math.abs(event.clientX - pointer.x);
  const deltaY = Math.abs(event.clientY - pointer.y);
  if (!pointer.horizontal && deltaY > 8 && deltaY > deltaX) { pointer = null; return; }
  if (!pointer.horizontal && deltaX > 8 && deltaX > deltaY) {
    pointer.horizontal = true;
    (event.currentTarget as SVGSVGElement).setPointerCapture?.(event.pointerId);
  }
  if (pointer.horizontal || event.pointerType === "mouse") select(event);
}
function up(): void { pointer = null; }

function keyboard(event: KeyboardEvent): void {
  const times = sampleTimes.value;
  if (!times.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const stamp = props.cursor;
  if (event.key === "Home") emit("cursor", times[0]);
  else if (event.key === "End") emit("cursor", times[times.length - 1]);
  else if (event.key === "ArrowRight") emit("cursor", times.find((time) => stamp === null || time > stamp) ?? times[times.length - 1]);
  else emit("cursor", [...times].reverse().find((time) => stamp === null || time < stamp) ?? times[0]);
}
</script>

<template>
  <article class="card time-chart">
    <div class="chart-heading">
      <h3>{{ caption }} <span>· {{ first.unit }}</span></h3>
      <p v-if="metrics.length === 1 && first.max !== null" class="chart-summary">
        {{ first.key === 'speed' ? '采样最高' : '采样范围' }} {{ first.key === 'speed' ? valueText(first, first.max) : `${valueText(first, first.min!)} ～ ${valueText(first, first.max)}` }}
      </p>
    </div>
    <div v-if="metrics.length > 1" class="chart-legend" role="group" :aria-label="caption">
      <button v-for="(metric, index) in metrics" :key="metric.key" type="button" :aria-pressed="!hidden.includes(metric.key)" :data-color="index % 4" @click="toggle(metric.key)">{{ metric.name }} · {{ metric.count }} 次采样</button>
    </div>
    <p v-if="!metrics.some(metric => metric.count)" class="note">暂无采样数据</p>
    <p v-else-if="!active.length" class="note">请开启一项指标</p>
    <svg v-else class="chart-svg" viewBox="0 0 640 220" tabindex="0" role="slider" :aria-label="`${caption}，左右方向键查看采样`" :aria-valuemin="data.start" :aria-valuemax="data.end" :aria-valuenow="cursor ?? data.start" :aria-valuetext="positionText" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up" @keydown="keyboard">
      <defs v-if="first.style === 'power'">
        <clipPath :id="`positive-${first.key}`"><rect x="76" y="18" width="546" :height="Math.max(0, y(0) - 18)" /></clipPath>
        <clipPath :id="`negative-${first.key}`"><rect x="76" :y="y(0)" width="546" :height="Math.max(0, 182 - y(0))" /></clipPath>
      </defs>
      <g class="chart-grid">
        <line v-for="fraction in [0, 0.5, 1]" :key="fraction" x1="76" x2="622" :y1="18 + fraction * 164" :y2="18 + fraction * 164" />
      </g>
      <g class="chart-axis">
        <template v-if="first.style === 'state'">
          <text x="70" :y="y(1) + 4" text-anchor="end">开</text>
          <text x="70" :y="y(0) + 4" text-anchor="end">关</text>
        </template>
        <text v-for="fraction in first.style === 'state' ? [] : [0, 0.5, 1]" :key="fraction" x="70" :y="22 + fraction * 164" text-anchor="end">{{ Number((domain[1] - fraction * (domain[1] - domain[0])).toFixed(first.unit === 'bar' ? 2 : 1)) }}</text>
        <text v-for="(tick, index) in ticks" :key="index" :x="x(tick)" y="208" :text-anchor="index === 0 ? 'start' : index === 2 ? 'end' : 'middle'">{{ timeText(tick) }}</text>
      </g>
      <rect v-for="(segment, index) in data.segments.slice(1)" :key="segment.index" class="chart-parking" :x="x(data.segments[index].end)" y="18" :width="Math.max(0, x(segment.start) - x(data.segments[index].end))" height="164"><title>停车</title></rect>
      <line v-if="first.style === 'power'" class="chart-zero" x1="76" x2="622" :y1="y(0)" :y2="y(0)" />
      <g v-for="metric in active" :key="metric.key" :data-color="metrics.indexOf(metric) % 4">
        <g v-for="(chunk, index) in metric.chunks" :key="index">
          <template v-if="metric.style === 'power' && chunk.points.length > 1">
            <path class="chart-positive" :d="area(metric, chunk.points)" :clip-path="`url(#positive-${first.key})`" />
            <path class="chart-negative" :d="area(metric, chunk.points)" :clip-path="`url(#negative-${first.key})`" />
          </template>
          <path class="chart-line" :d="path(metric, chunk.points)" />
          <circle v-for="point in chunk.points.length <= 32 ? chunk.points : []" :key="point[0]" class="chart-point" :cx="x(point[0])" :cy="y(point[1])" :r="chunk.points.length === 1 ? 4 : 2.5" @click.stop="emit('cursor', point[0])"><title>{{ timeText(point[0]) }} · {{ valueText(metric, point[1]) }}</title></circle>
        </g>
        <line v-for="stamp in metric.anomalies" :key="stamp" class="chart-anomaly" :x1="x(stamp)" :x2="x(stamp)" y1="18" y2="182"><title>累计值下降／重置</title></line>
      </g>
      <line v-if="cursor !== null" class="chart-cursor" :x1="x(cursor)" :x2="x(cursor)" y1="18" y2="182" />
    </svg>
    <div v-if="cursor !== null" class="chart-readings" aria-live="polite">
      <p>{{ timeText(cursor) }} · {{ data.kind === 'drive' ? (data.segments.some(segment => cursor! >= segment.start && cursor! <= segment.end) ? '驾驶中' : '停车') : '充电会话' }}</p>
      <p v-for="{ metric, reading } in readings" :key="metric.key">
        {{ metric.name }}：{{ reading ? valueText(metric, reading.point[1]) : '无采样' }}
        <span v-if="reading">· 最近保留采样 {{ timeText(reading.point[0]) }}{{ data.kind === 'drive' ? ` · 第 ${reading.segment} 段` : '' }} · 相差 {{ Number((Math.abs(reading.point[0] - cursor) / 1000).toFixed(3)) }} 秒</span>
      </p>
    </div>
    <p v-if="first.anomalies.length" class="note chart-warning">数据异常：累计值下降／重置 {{ first.anomalies.length }} 次，已断线。</p>
    <p v-if="first.note" class="note">{{ first.note }}</p>
    <p v-if="first.style === 'power'" class="chart-footnote">零线上方／橙色面积：输出（+）；零线下方／绿色面积：回收（−）。</p>
    <p class="chart-footnote">{{ first.count }} 次有效采样{{ first.style === 'state' ? ' · 仅连接有记录的状态，缺失及长缺口为未知，不外推末次状态。' : ' · 长缺口断线，不向段首尾外推。' }}</p>
  </article>
</template>
