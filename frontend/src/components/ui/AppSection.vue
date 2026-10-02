<script setup lang="ts">
defineProps<{
  title?: string;
  description?: string;
  dense?: boolean;
}>();
</script>

<template>
  <section class="app-section" :class="{ 'app-section--dense': dense }">
    <header v-if="title || $slots.actions" class="app-section__head">
      <div class="app-section__titles">
        <h2 v-if="title" class="app-section__title">{{ title }}</h2>
        <p v-if="description" class="app-section__desc">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="app-section__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="app-section__body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.app-section {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.app-section--dense {
  gap: var(--sp-2);
}

.app-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

.app-section__title {
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-text);
}

.app-section__desc {
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  margin-top: 2px;
}

.app-section__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-shrink: 0;
}

.app-section__body {
  min-width: 0;
}
</style>
