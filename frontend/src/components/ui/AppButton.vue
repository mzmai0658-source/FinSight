<script setup lang="ts">
defineProps<{
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  block?: boolean;
  disabled?: boolean;
  loading?: boolean;
  type?: "button" | "submit" | "reset";
}>();
</script>

<template>
  <button
    class="app-btn"
    :class="[
      `app-btn--${variant ?? 'primary'}`,
      `app-btn--${size ?? 'md'}`,
      { 'app-btn--block': block },
    ]"
    :disabled="disabled || loading"
    :type="type ?? 'button'"
  >
    <span v-if="loading" class="app-btn__spinner" />
    <slot />
  </button>
</template>

<style scoped>
.app-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: var(--btn-radius);
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast),
    opacity var(--t-fast), box-shadow var(--t-fast);
}

.app-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.app-btn--sm {
  height: var(--btn-height-sm);
  padding: 0 var(--sp-3);
  font-size: var(--fs-xs);
}

.app-btn--md {
  height: var(--btn-height);
  padding: 0 var(--sp-4);
  font-size: var(--fs-sm);
}

.app-btn--lg {
  height: 44px;
  padding: 0 var(--sp-6);
  font-size: var(--fs-md);
}

.app-btn--block {
  width: 100%;
}

.app-btn--primary {
  background: var(--c-primary);
  color: #fff;
  box-shadow: 0 1px 2px rgb(36 86 196 / 18%);
}

.app-btn--primary:hover:not(:disabled) {
  background: var(--c-primary-hover);
}

.app-btn--secondary {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.app-btn--secondary:hover:not(:disabled) {
  background: var(--c-primary-border);
}

.app-btn--outline {
  background: var(--c-surface);
  border-color: var(--c-border-strong);
  color: var(--c-text);
}

.app-btn--outline:hover:not(:disabled) {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

.app-btn--ghost {
  background: transparent;
  color: var(--c-text-secondary);
}

.app-btn--ghost:hover:not(:disabled) {
  background: var(--c-bg);
  color: var(--c-text);
}

.app-btn--danger {
  background: var(--c-surface);
  border-color: var(--c-border-strong);
  color: var(--c-danger);
}

.app-btn--danger:hover:not(:disabled) {
  background: var(--c-danger-soft);
  border-color: var(--c-danger);
}

.app-btn__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
</style>
