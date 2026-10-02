/** 作品说明：ECharts 主题工具：与设计令牌保持一致的克制金融风格 */

export const PALETTE = ["#2456c4", "#1a8f5c", "#b97d10", "#c2403a", "#4f7fd9", "#6c5dd3"];

export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: "#d0d6df" } },
  axisLabel: { color: "#5c6470", fontSize: 12 },
  splitLine: { lineStyle: { color: "#eef0f4" } },
} as const;

export const TOOLTIP_STYLE = {
  trigger: "axis" as const,
  backgroundColor: "#ffffff",
  borderColor: "#e3e7ee",
  textStyle: { color: "#1a2233", fontSize: 12 },
  confine: true,
  extraCssText: "box-shadow: 0 4px 16px rgba(20,30,50,0.08); border-radius: 8px;",
};

export const LEGEND_STYLE = {
  type: "scroll",
  textStyle: { color: "#5c6470", fontSize: 12 },
  itemWidth: 14,
  itemHeight: 8,
} as const;

export const GRID_STYLE = { left: 12, right: 18, top: 54, bottom: 10, containLabel: true } as const;

/** 作品说明：万元 → 亿元（保留 2 位） */
export function toYi(value: unknown): number | null {
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  return Math.round((num / 10000) * 100) / 100;
}

/** 作品说明：数字安全转换（null 保留为 null，断点不连线） */
export function toNum(value: unknown): number | null {
  const num = Number(value);
  return Number.isFinite(num) ? Math.round(num * 100) / 100 : null;
}

export function fmtYi(value: unknown): string {
  const v = toYi(value);
  return v === null ? "—" : `${v.toFixed(2)} 亿`;
}

export function fmtPct(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${num.toFixed(2)}%` : "—";
}

export function fmtCount(value: unknown, unit = "家"): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${Math.round(num).toLocaleString("zh-CN")} ${unit}` : "—";
}

export function fmtTimes(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${Math.round(num).toLocaleString("zh-CN")} 次` : "—";
}

export function fmtChartYi(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${num.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 亿元` : "—";
}

export function fmtChartPct(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${num.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%` : "—";
}

export function axisYi(value: number): string {
  return `${value} 亿`;
}

export function axisPct(value: number): string {
  return `${value}%`;
}

export function axisCount(value: number): string {
  return `${value} 家`;
}
