<script setup lang="ts">
type BadgeVariant = "default" | "primary" | "success" | "warning" | "danger" | "info";
type BadgeSize = "sm" | "md";

withDefaults(
  defineProps<{
    variant?: BadgeVariant;
    size?: BadgeSize;
    dot?: boolean;
  }>(),
  { variant: "default", size: "sm", dot: false },
);
</script>

<template>
  <span
    class="app-badge"
    :class="[`app-badge--${variant}`, `app-badge--${size}`, { 'app-badge--dot': dot }]"
  >
    <span v-if="dot" class="app-badge__dot" />
    <slot />
  </span>
</template>

<style scoped>
.app-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: var(--r-full);
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
}

.app-badge--sm {
  font-size: var(--fs-xs);
  padding: 1px 8px;
  height: 18px;
}

.app-badge--md {
  font-size: var(--fs-sm);
  padding: 2px 10px;
  height: 22px;
}

.app-badge--default {
  background: var(--c-bg);
  color: var(--c-text-secondary);
  border: 1px solid var(--c-border);
}

.app-badge--primary {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.app-badge--success {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.app-badge--warning {
  background: var(--c-warning-soft);
  color: var(--c-warning);
}

.app-badge--danger {
  background: var(--c-danger-soft);
  color: var(--c-danger);
}

.app-badge--info {
  background: var(--c-info-soft);
  color: var(--c-primary);
}

.app-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
</style>
