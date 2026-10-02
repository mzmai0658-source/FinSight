<script setup lang="ts">
import { computed } from "vue";

import { IconAlert, IconCheck } from "@/components/ui/icons";
import type { VerificationResult } from "@/types/api";

const props = defineProps<{
  verification: VerificationResult | null;
  interactive?: boolean;
}>();

const emit = defineEmits<{ openDetail: [] }>();

const status = computed(() => props.verification?.status ?? "unverified");
const label = computed(() => ({
  pass: props.verification?.version === 3 ? "程序条件与来源检查通过" : "旧版数字一致性核验",
  warn: "有一部分还对不上",
  fail: "这次没对上",
  unverified: "这次还没有自动核对",
}[status.value]));

function openDetail() {
  if (props.interactive) emit("openDetail");
}
</script>

<template>
  <button
    class="verification-badge"
    :class="[`verification-badge--${status}`, { 'verification-badge--interactive': interactive }]"
    type="button"
    :title="label"
    :aria-label="label"
    @click="openDetail"
  >
    <IconCheck v-if="status === 'pass'" :size="13" />
    <IconAlert v-else :size="13" />
    <span>{{ label }}</span>
  </button>
</template>

<style scoped>
.verification-badge {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 26px;
  padding: 0 9px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface-muted);
  color: var(--c-text-secondary);
  font-size: var(--fs-xs);
  font-weight: 500;
}

.verification-badge--interactive {
  cursor: pointer;
}

.verification-badge--interactive:hover {
  filter: brightness(0.97);
}

.verification-badge--pass {
  color: var(--c-success);
  background: var(--c-success-soft);
  border-color: color-mix(in srgb, var(--c-success) 28%, transparent);
}

.verification-badge--warn,
.verification-badge--unverified {
  color: var(--c-warning);
  background: var(--c-warning-soft);
  border-color: color-mix(in srgb, var(--c-warning) 28%, transparent);
}

.verification-badge--fail {
  color: var(--c-danger);
  background: var(--c-danger-soft);
  border-color: color-mix(in srgb, var(--c-danger) 28%, transparent);
}
</style>
