<script setup lang="ts">
import AppEmpty from "./AppEmpty.vue";

defineProps<{
  emptyTitle?: string;
  emptyDesc?: string;
  loading?: boolean;
}>();
</script>

<template>
  <div class="app-table-wrap">
    <table class="app-table">
      <slot />
    </table>
    <AppEmpty
      v-if="!loading && $slots.empty"
      :title="emptyTitle"
      :description="emptyDesc"
      class="app-table__empty"
    >
      <slot name="empty" />
    </AppEmpty>
  </div>
</template>

<style>
.app-table-wrap {
  width: 100%;
  overflow-x: auto;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: var(--card-radius);
}

.app-table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.app-table th {
  text-align: left;
  padding: 12px var(--sp-4);
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--c-text-tertiary);
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
  background: var(--c-bg);
}

.app-table td {
  padding: 14px var(--sp-4);
  border-bottom: 1px solid var(--c-border);
  color: var(--c-text-secondary);
  vertical-align: middle;
}

.app-table tbody tr:last-child td {
  border-bottom: none;
}

.app-table tbody tr {
  transition: background var(--t-fast);
}

.app-table tbody tr:hover {
  background: var(--c-primary-soft);
}

.app-table__empty {
  padding: var(--sp-8) 0;
}
</style>
