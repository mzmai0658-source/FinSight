<script setup lang="ts">
defineProps<{
  lines?: number;
  avatar?: boolean;
}>();
</script>

<template>
  <div class="app-skeleton">
    <div v-if="avatar" class="app-skeleton__avatar" />
    <div class="app-skeleton__lines">
      <div
        v-for="i in lines ?? 3"
        :key="i"
        class="app-skeleton__line"
        :style="{ width: i % 3 === 1 ? '60%' : i % 3 === 2 ? '85%' : '40%' }"
      />
    </div>
  </div>
</template>

<style scoped>
.app-skeleton {
  display: flex;
  gap: var(--sp-3);
  width: 100%;
}

.app-skeleton__avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--r-md);
  background: linear-gradient(90deg, var(--c-border) 25%, var(--c-surface-muted) 50%, var(--c-border) 75%);
  background-size: 200% 100%;
  animation: skeleton 1.4s ease infinite;
}

.app-skeleton__lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.app-skeleton__line {
  height: 12px;
  border-radius: var(--r-full);
  background: linear-gradient(90deg, var(--c-border) 25%, var(--c-surface-muted) 50%, var(--c-border) 75%);
  background-size: 200% 100%;
  animation: skeleton 1.4s ease infinite;
}

@keyframes skeleton {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
