<script setup lang="ts">
import { computed } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { DatasetComponent, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from "echarts/components";

import type { ChartData } from "@/types/api";
import RegisteredAsset from "./RegisteredAsset.vue";

const props = defineProps<{
  chartData?: ChartData | null;
  imageUrl?: string;
  title?: string;
  live?: boolean;
}>();
const emit = defineEmits<{ openSource: [queryId: string] }>();

use([CanvasRenderer, BarChart, LineChart, PieChart, ScatterChart, DatasetComponent, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent]);

const captionTitle = computed(() => props.title || props.chartData?.title || "");
const unitText = computed(() => props.chartData?.unit || props.chartData?.data_source?.unit || normalizeUnit(props.chartData?.y_label));

function normalizeUnit(label?: string): string {
  const raw = String(label ?? "").trim();
  if (!raw) return "";
  if (raw.includes("%") || raw.includes("率") || raw.includes("同比")) return "%";
  if (raw.includes("亿元") || raw === "亿" || raw.includes("亿")) return "亿元";
  if (raw.includes("万元") || raw === "万") return "万元";
  if (raw.includes("家")) return "家";
  if (raw.includes("次")) return "次";
  return raw;
}

function formatValue(value: unknown, unit: string): string {
  if (value === null || value === undefined || value === "") return "无可用值";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  const text = num.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return unit ? `${text} ${unit}` : text;
}

const chartOption = computed(() => {
  if (!props.chartData) return {};
  if (props.chartData.option) {
    const option = JSON.parse(JSON.stringify(props.chartData.option)) as Record<string, unknown>;
    if (props.chartData.version === 3) {
      const safe = (text: unknown) => String(text ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]!));
      option.tooltip = { ...(option.tooltip as Record<string, unknown>), confine:true,
        formatter: (raw: unknown) => {
          const items = (Array.isArray(raw) ? raw : [raw]) as Array<{ name?:string;seriesName?:string;data?:{value_formatted?:string|string[];value?:unknown} }>;
          return items.map((item) => `${safe(item.name || item.seriesName)}: ${safe(Array.isArray(item.data?.value_formatted) ? item.data.value_formatted.join("，") : item.data?.value_formatted || "暂无核实值")}`).join("<br>");
        } };
    }
    return option;
  }
  const { chart_type, title, x_data, y_data, y_label, series_name } = props.chartData;
  const baseColor = "#2456c4";
  const unit = unitText.value;

  const option: Record<string, unknown> = {
    dataset: { dimensions: ["label", "value"], source: x_data.map((label, index) => ({ label, value: y_data[index] ?? null })) },
    tooltip: {
      trigger: chart_type === "pie" ? "item" : "axis",
      confine: true,
      valueFormatter: (value: unknown) => formatValue(value, unit),
    },
    grid: { left: 52, right: 20, top: 26, bottom: x_data.length > 6 ? 56 : 34, containLabel: true },
  };

  if (chart_type === "pie") {
    option.series = [
      {
        type: "pie",
        radius: ["38%", "66%"],
        encode: { itemName: "label", value: "value" },
        avoidLabelOverlap: true,
        minAngle: 4,
        label: { formatter: "{b}: {d}%", overflow: "break" },
      },
    ];
  } else {
    option.xAxis = {
      type: "category",
      axisLabel: { rotate: x_data.length > 6 ? 30 : 0, color: "#5c6470", fontSize: 12 },
    };
    option.yAxis = {
      type: "value",
      name: unit,
      axisLabel: {
        color: "#5c6470",
        fontSize: 12,
        formatter: (value: number) => formatValue(value, unit),
      },
      splitLine: { lineStyle: { color: "#eef0f4" } },
    };
    option.series = [
      {
        type: chart_type === "line" ? "line" : "bar",
        name: series_name ?? title,
        encode: { x: "label", y: "value" },
        itemStyle: { color: baseColor },
        ...(chart_type === "line"
          ? { smooth: false, connectNulls: false, symbolSize: 7 }
          : { barMaxWidth: 36, itemStyle: { color: baseColor, borderRadius: [4, 4, 0, 0] } }),
      },
    ];
    if (x_data.length > 10) {
      option.dataZoom = [{ type: "inside", start: 0, end: 80 }];
    }
  }

  return option;
});
</script>

<template>
  <figure class="chart-card">
    <figcaption v-if="captionTitle || unitText" class="chart-card__caption">
      <strong v-if="captionTitle">{{ captionTitle }}</strong>
      <span v-if="unitText">单位：{{ unitText }}</span>
    </figcaption>
    <VChart v-if="chartData" class="chart-card__canvas" :option="chartOption" autoresize />
    <RegisteredAsset v-else-if="imageUrl" :url="imageUrl" :disabled="live" image :label="title || '图表'" />
    <div v-if="chartData?.data_source" class="chart-card__source">
      <span>数据来源：{{ chartData.data_source.label || chartData.data_source.title || chartData.data_source.kind || '已登记证据' }}</span>
      <span v-if="chartData.data_source.y_field">字段 {{ chartData.data_source.y_field }} · {{ chartData.data_source.unit || unitText }}</span>
      <button v-if="chartData.data_source.query_id" type="button" @click="emit('openSource', chartData.data_source.query_id)">查看关联查询 {{ chartData.data_source.query_id }}</button>
    </div>
  </figure>
</template>

<style scoped>
.chart-card__source { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: var(--fs-xs); color: var(--c-text-secondary); }
.chart-card__source button { cursor: pointer; color: var(--c-primary); background: none; border: 0; }
.chart-card {
  margin: 0;
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  background: var(--c-surface);
  padding: var(--sp-3);
}

.chart-card__caption {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}

.chart-card__caption strong {
  min-width: 0;
  color: var(--c-text);
  font-size: var(--fs-sm);
  line-height: 1.45;
}

.chart-card__caption span {
  flex: 0 0 auto;
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
}

.chart-card__canvas {
  width: 100%;
  height: 300px;
}

.chart-card__image {
  display: block;
  width: 100%;
  border-radius: var(--r-sm);
}

@media (max-width: 520px) {
  .chart-card__caption {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }

  .chart-card__canvas {
    height: 260px;
  }
}
</style>
