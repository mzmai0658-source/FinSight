<script setup lang="ts">
defineProps<{
  hover?: boolean;
  clickable?: boolean;
  padding?: "none" | "sm" | "md" | "lg";
}>();
</script>

<template>
  <div
    class="app-card"
    :class="{
      'app-card--hover': hover,
      'app-card--clickable': clickable,
      [`app-card--pad-${padding ?? 'md'}`]: true,
    }"
  >
    <div v-if="$slots.header" class="app-card__header">
      <slot name="header" />
    </div>
    <div v-if="$slots.default" class="app-card__body">
      <slot />
    </div>
    <div v-if="$slots.footer" class="app-card__footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.app-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: var(--card-radius);
  box-shadow: var(--card-shadow);
  display: flex;
  flex-direction: column;
  transition: border-color var(--t-fast), box-shadow var(--t-fast), transform var(--t-fast);
}

.app-card--pad-sm {
  padding: var(--sp-3);
}

.app-card--pad-md {
  padding: var(--sp-4) var(--sp-5);
}

.app-card--pad-lg {
  padding: var(--sp-6);
}

.app-card--pad-none {
  padding: 0;
}

.app-card--hover:hover,
.app-card--clickable:hover {
  border-color: var(--c-primary-border);
  box-shadow: var(--card-hover-shadow);
}

.app-card--clickable {
  cursor: pointer;
}

.app-card--clickable:hover {
  transform: translateY(-1px);
}

.app-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-3);
}

.app-card__body {
  flex: 1;
  min-width: 0;
}

.app-card__footer {
  margin-top: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--c-border);
}
</style>
