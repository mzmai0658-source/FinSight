<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import AppButton from "@/components/ui/AppButton.vue";
import { fetchFinancialSubmissionOriginal } from "@/services/api";

const props = defineProps<{ submissionId: number }>();
const emit = defineEmits<{ opened: [] }>();
const objectUrl = ref("");
const page = ref(1);
const loading = ref(false);
const error = ref("");
const href = computed(() => objectUrl.value ? `${objectUrl.value}#page=${page.value}` : "");
let generation = 0;
function release() {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value);
  objectUrl.value = "";
}
async function open(targetPage = 1) {
  page.value = Number.isInteger(targetPage) && targetPage > 0 ? targetPage : 1;
  if (objectUrl.value || loading.value) return;
  const request = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const blob = await fetchFinancialSubmissionOriginal(props.submissionId);
    if (request !== generation) return;
    objectUrl.value = URL.createObjectURL(blob);
    emit("opened");
  } catch (cause) {
    if (request === generation) error.value = cause instanceof Error ? cause.message : "原始 PDF 加载失败，请重试。";
  } finally {
    if (request === generation) loading.value = false;
  }
}
watch(() => props.submissionId, () => { generation += 1; release(); page.value = 1; error.value = ""; loading.value = false; });
onBeforeUnmount(() => { generation += 1; release(); });
defineExpose({ open });
</script>

<template>
  <section class="original" aria-label="原始财报 PDF">
    <div class="original__toolbar">
      <div><h3>原始 PDF</h3><p>核对公司名称、报告期与候选数字的原文位置。</p></div>
      <a v-if="objectUrl" :href="href" target="_blank" rel="noopener noreferrer">在新标签页查看 PDF</a>
      <AppButton v-else variant="outline" :loading="loading" @click="open()">{{ loading ? "正在加载 PDF…" : "查看原始 PDF" }}</AppButton>
    </div>
    <p v-if="error" class="original__error" role="alert">{{ error }} <button type="button" @click="open(page)">重试原件</button></p>
    <iframe v-if="objectUrl" :src="href" title="原始财报 PDF" />
  </section>
</template>

<style scoped>
.original { display: grid; gap: var(--sp-3); }
.original__toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--sp-3); }
h3 { font-size: var(--fs-md); color: var(--c-ink); }
p, a { font-size: var(--fs-sm); line-height: 1.7; color: var(--c-text-secondary); }
a { color: var(--c-primary); }
iframe { width: 100%; height: min(70dvh, 640px); min-height: 320px; border: 1px solid var(--c-border); border-radius: var(--r-md); }
.original__error { color: var(--c-danger); }
.original__error button { color: inherit; background: none; border: 0; text-decoration: underline; cursor: pointer; }
</style>
