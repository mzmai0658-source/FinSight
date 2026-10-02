<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { fetchFinancialPdf, fetchRegisteredAsset, registeredAssetId } from "@/services/api";

const props = defineProps<{
  assetId?: string;
  url?: string | null;
  page?: number | null;
  image?: boolean;
  label?: string;
  disabled?: boolean;
  stockCode?: string;
  reportYear?: number | null;
  reportPeriod?: string;
}>();
const id = computed(() => registeredAssetId(props.assetId || props.url));
const canOpenReport = computed(() => Boolean(props.stockCode && props.reportYear && props.reportPeriod));
const objectUrl = ref("");
const error = ref("");
const loading = ref(false);
let generation = 0;
function release() {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value);
  objectUrl.value = "";
}
function pageHref(url: string) {
  return url + (props.page && props.page > 0 ? `#page=${Math.trunc(props.page)}` : "");
}
function openInNewTab() {
  if (props.disabled || loading.value || objectUrl.value) return;
  const popup = props.image ? null : window.open("", "_blank");
  void load(popup);
}
async function load(popup?: Window | null) {
  if (props.disabled || loading.value || objectUrl.value) return;
  if (!id.value && !canOpenReport.value) return;
  loading.value = true;
  error.value = "";
  const requestGeneration = ++generation;
  try {
    const blob = id.value ? await fetchRegisteredAsset(id.value)
      : await fetchFinancialPdf({
          stockCode: props.stockCode || "",
          company: "",
          reportYear: props.reportYear ?? null,
          reportPeriod: props.reportPeriod || "",
          fileName: "",
          pageCount: null,
          sha256: null,
          status: "imported",
          pdfAvailable: true,
        });
    if (requestGeneration !== generation) return;
    if (props.image && !blob.type.startsWith("image/")) throw new Error("此资产不是可显示的图片");
    const url = URL.createObjectURL(blob);
    if (popup && !popup.closed) {
      const frame = pageHref(url).replace(/"/g, "");
      popup.document.open();
      popup.document.write(
        `<!DOCTYPE html><title>${(props.label || "原文").replace(/[<>]/g, "")}</title>` +
        `<style>html,body{margin:0;height:100%;background:#111}iframe{border:0;width:100%;height:100%}</style>` +
        `<iframe src="${frame}"></iframe>`,
      );
      popup.document.close();
      return;
    }
    objectUrl.value = url;
  } catch (e) {
    if (popup && !popup.closed) popup.close();
    if (requestGeneration === generation) error.value = e instanceof Error ? e.message : "无法打开原文，请重新登录或重试";
  } finally {
    if (requestGeneration === generation) loading.value = false;
  }
}
watch(() => [id.value, props.disabled], () => {
  generation += 1;
  loading.value = false;
  release();
  if (props.image && !props.disabled) load();
}, { immediate: true });
onBeforeUnmount(() => { generation += 1; release(); });
const href = computed(() => objectUrl.value + (props.page && props.page > 0 ? `#page=${Math.trunc(props.page)}` : ""));
</script>

<template>
  <div class="registered-asset">
    <img v-if="image && objectUrl" :src="objectUrl" :alt="label || '证据图表'" />
    <a v-else-if="objectUrl" :href="href" target="_blank" rel="noopener noreferrer">{{ label || '打开原文' }}</a>
    <button v-else-if="id || canOpenReport" type="button" :disabled="disabled || loading" @click="openInNewTab">
      {{ disabled ? '回答保存后可查看原文' : loading ? '正在加载…' : label || '加载原文' }}
    </button>
    <span v-else>原文定位暂不可用</span>
    <span v-if="error" role="alert">{{ error }}</span>
  </div>
</template>

<style scoped>
.registered-asset { display: grid; gap: 5px; font-size: var(--fs-xs); }
.registered-asset img { max-width: 100%; border-radius: var(--r-sm); }
.registered-asset button { justify-self: start; cursor: pointer; color: var(--c-primary); background: none; border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 5px 8px; }
.registered-asset span { color: var(--c-text-secondary); }
.registered-asset [role=alert] { color: var(--c-danger); }
</style>
