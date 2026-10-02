<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{ open: boolean; label: string; side?: "left" | "right" }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);

watch(() => props.open, (open) => {
  if (open && !dialog.value?.open) dialog.value?.showModal();
  if (!open && dialog.value?.open) dialog.value.close();
}, { flush: "post", immediate: true });
// 作品说明：已打开的父组件渲染后，弹窗元素可能才挂载，需同步其打开状态。
watch(dialog, (el) => { if (el && props.open && !el.open) el.showModal(); });
onBeforeUnmount(() => dialog.value?.close());
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="app-dialog" :class="side ? `app-dialog--${side}` : ''"
      role="dialog" :aria-label="label" aria-modal="true"
      @cancel.prevent="emit('close')" @click.self="emit('close')">
      <div v-if="open" class="app-dialog__body"><slot /></div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.app-dialog {
  max-width: none;
  max-height: none;
  width: min(900px, calc(100% - 32px));
  padding: 0;
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  background: var(--c-surface);
  color: var(--c-text);
  box-shadow: var(--shadow-lg);
  overflow: visible;
}
.app-dialog::backdrop { background: rgb(15 23 42 / 45%); }
.app-dialog__body { max-height: 90dvh; overflow: auto; overscroll-behavior: contain; border-radius: inherit; }
.app-dialog--left, .app-dialog--right {
  width: min(384px, calc(100% - 24px));
  height: 100dvh;
  border-radius: 0;
  margin: 0;
}
.app-dialog--left { margin-right: auto; }
.app-dialog--right { margin-left: auto; }
.app-dialog--left .app-dialog__body, .app-dialog--right .app-dialog__body { height: 100%; max-height: none; overflow: hidden; }
@media (max-width: 640px) {
  .app-dialog:not(.app-dialog--left):not(.app-dialog--right) { width: calc(100% - 16px); }
  .app-dialog__body { max-height: 94dvh; }
}
</style>
