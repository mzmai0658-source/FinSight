<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";

const props = defineProps<{
  option: echarts.EChartsOption;
  height?: string;
}>();

const emit = defineEmits<{
  click: [params: { name?: string; seriesName?: string; value?: unknown }];
}>();

const el = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;
let observer: ResizeObserver | null = null;

function render() {
  if (!chart && el.value) {
    chart = echarts.init(el.value);
    chart.on("click", (params) => {
      emit("click", { name: params.name, seriesName: params.seriesName, value: params.value });
    });
  }
  chart?.setOption(props.option, { notMerge: true });
}

onMounted(() => {
  render();
  observer = new ResizeObserver(() => chart?.resize());
  if (el.value) observer.observe(el.value);
});

watch(
  () => props.option,
  () => render(),
  { deep: true },
);

onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div ref="el" class="echart" :style="{ height: height ?? '280px' }" />
</template>

<style scoped>
.echart {
  width: 100%;
  min-width: 0;
  min-height: 240px;
}
</style>
