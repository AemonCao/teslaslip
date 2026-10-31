export type Point = [number, number];
export interface Chunk {
  segment: number;
  threshold: number;
  points: Point[];
}
export interface Metric {
  key: string;
  name: string;
  unit: string;
  group: string;
  style: "line" | "step" | "power" | "state";
  extra: boolean;
  note: string;
  rangeMode: "rated" | "ideal" | null;
  chunks: Chunk[];
  count: number;
  min: number | null;
  max: number | null;
  anomalies: number[];
}
export interface Segment { index: number; start: number; end: number }
export interface Distribution {
  bins: { label: string; seconds: number; percent: number }[];
  coveredSeconds: number;
  uncoveredSeconds: number;
  waiting: { segment: number; start: number; end: number; seconds: number }[];
}
export interface ChartData {
  kind: "drive" | "charge";
  start: number;
  end: number;
  segments: Segment[];
  metrics: Metric[];
  distribution: Distribution | null;
}

const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const calendar = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
});

export function timeText(stamp: number): string { return clock.format(stamp); }
export function dateText(stamp: number): string { return calendar.format(stamp); }
export function durationText(seconds: number): string {
  if (seconds < 60) return `${Number(seconds.toFixed(1))} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Number((seconds % 60).toFixed(1))} 秒`;
}

export function valueText(metric: Metric, value: number): string {
  if (metric.style === "state") return value === 1 ? "开" : "关";
  const decimals = metric.unit === "%" || metric.unit === "km/h" ? 0 : metric.unit === "bar" || metric.unit === "kWh" ? 2 : 1;
  const text = String(Number(value.toFixed(decimals)));
  return `${metric.style === "power" && value > 0 ? "+" : ""}${text} ${metric.unit}`;
}

export function nearest(metric: Metric, stamp: number): { point: Point; segment: number } | null {
  let selected: { point: Point; segment: number } | null = null;
  for (const chunk of metric.chunks) {
    const points = chunk.points;
    if (!points.length || stamp < points[0][0] || stamp > points[points.length - 1][0]) continue;
    let lower = 0;
    let upper = points.length - 1;
    while (lower < upper) {
      const middle = Math.floor((lower + upper) / 2);
      if (points[middle][0] < stamp) lower = middle + 1;
      else upper = middle;
    }
    const next = points[lower];
    const previous = points[Math.max(0, lower - 1)];
    const point = stamp - previous[0] <= next[0] - stamp ? previous : next;
    if (!selected || Math.abs(point[0] - stamp) < Math.abs(selected.point[0] - stamp)) selected = { point, segment: chunk.segment };
  }
  return selected;
}

export function linePath(points: Point[], x: (value: number) => number, y: (value: number) => number, step: boolean): string {
  return points.map((point, index) => `${index ? step ? "H" : "L" : "M"}${x(point[0]).toFixed(2)}${index && step ? "V" : ","}${y(point[1]).toFixed(2)}`).join(" ");
}

export function yDomain(metrics: Metric[]): [number, number] {
  const values = metrics.flatMap((metric) => metric.min === null || metric.max === null ? [] : [metric.min, metric.max]);
  if (!values.length) return [0, 1];
  if (metrics[0].style === "state") return [-0.2, 1.2];
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (metrics.some((metric) => metric.style === "power") || metrics[0].key === "speed" || metrics[0].key === "charger_power") {
    minimum = Math.min(minimum, 0);
    maximum = Math.max(maximum, 0);
  }
  const padding = Math.max((maximum - minimum) * 0.08, metrics[0].unit === "bar" ? 0.01 : 1);
  return [minimum - padding, maximum + padding];
}
