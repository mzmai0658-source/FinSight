<script setup lang="ts">
defineProps<{
  title?: string;
  description?: string;
}>();
</script>

<template>
  <div class="app-empty">
    <div class="app-empty__icon">
      <slot name="icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M9 14 4 9l5-5" />
          <path d="M4 9h10.5a3.5 3.5 0 0 1 3.5 3.5v0a3.5 3.5 0 0 1-3.5 3.5H11" />
        </svg>
      </slot>
    </div>
    <p v-if="title" class="app-empty__title">{{ title }}</p>
    <p v-if="description" class="app-empty__desc">{{ description }}</p>
    <slot />
  </div>
</template>

<style scoped>
.app-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--sp-10) var(--sp-4);
  color: var(--c-text-tertiary);
}

.app-empty__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: var(--r-lg);
  background: var(--c-surface-muted);
  color: var(--c-text-tertiary);
  margin-bottom: var(--sp-3);
}

.app-empty__title {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--c-text);
  margin-bottom: var(--sp-1);
}

.app-empty__desc {
  font-size: var(--fs-sm);
  max-width: 320px;
  line-height: 1.55;
}
</style>
