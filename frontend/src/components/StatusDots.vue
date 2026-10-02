<script setup lang="ts">
import { computed } from "vue";

import type { HealthResponse } from "@/types/api";

const props = defineProps<{ health: HealthResponse | null }>();

const items = computed(() => [
  { key: "service", label: "API", ok: props.health?.service?.ok ?? false, detail: props.health?.service?.detail, warning: false },
  { key: "database", label: "数据库", ok: props.health?.database?.ok ?? false, detail: props.health?.database?.detail, warning: false },
  { key: "knowledge_base", label: "知识库", ok: props.health?.knowledge_base?.ok ?? false, detail: props.health?.knowledge_base?.detail, warning: false },
  { key: "llm", label: "模型", ok: props.health?.llm?.ok ?? false, detail: props.health?.llm?.detail,
    warning: props.health?.llm?.detail?.includes("低于建议") ?? false },
]);
</script>

<template>
  <div class="status-dots">
    <span v-for="item in items" :key="item.key" class="status-dots__item" :title="item.detail || item.label">
      <span class="status-dots__dot" :class="{ 'status-dots__dot--ok': item.ok, 'status-dots__dot--warn': item.warning }" />
      {{ item.label }}
    </span>
  </div>
</template>

<style scoped>
.status-dots {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.status-dots__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.status-dots__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--c-danger);
}

.status-dots__dot--ok {
  background: var(--c-success);
}
.status-dots__dot--warn {
  background: var(--c-warning);
}
</style>
