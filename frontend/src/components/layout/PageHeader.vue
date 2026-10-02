<script setup lang="ts">
defineProps<{
  title: string;
  description?: string;
  eyebrow?: string;
}>();
</script>

<template>
  <header class="page-header">
    <div class="page-header__text">
      <span v-if="eyebrow" class="page-header__eyebrow">{{ eyebrow }}</span>
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="description" class="page-header__desc">{{ description }}</p>
      <slot name="below" />
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-6);
  flex-wrap: wrap;
}

.page-header__text {
  min-width: 0;
  flex: 1 1 420px;
}

.page-header__eyebrow {
  display: block;
  margin-bottom: var(--sp-2);
  font-size: var(--fs-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--c-primary);
}

.page-header__title {
  font-size: var(--fs-2xl);
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: -0.3px;
  color: var(--c-ink);
}

.page-header__desc {
  margin-top: var(--sp-2);
  max-width: 720px;
  font-size: var(--fs-md);
  line-height: 1.7;
  color: var(--c-text-secondary);
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex: 0 0 auto;
}

@media (max-width: 640px) {
  .page-header__title {
    font-size: var(--fs-xl);
  }

  .page-header__desc {
    font-size: var(--fs-sm);
  }

  .page-header__actions {
    width: 100%;
  }
}
</style>
