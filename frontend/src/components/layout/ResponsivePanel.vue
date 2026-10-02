<script setup lang="ts">
import AppDialog from "@/components/ui/AppDialog.vue";
defineProps<{ overlay: boolean; open: boolean; label: string; side: "left" | "right" }>();
defineEmits<{ close: [] }>();
</script>

<template>
  <AppDialog v-if="overlay" :open="open" :label="label" :side="side" @close="$emit('close')">
    <slot />
  </AppDialog>
  <slot v-else />
</template>
