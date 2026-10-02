<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { fetchResearchPage, fetchFinancialPage, loadStoredAuth, type ResearchPage } from '@/services/api';
import { renderOcrMarkdown } from '@/utils/markdown';
const props = defineProps<{ reportId: number; kind?: 'research' | 'financial' }>();
const data = ref<ResearchPage | null>(null), loading = ref(false), error = ref(''), requestedPage = ref(1);
const loggedIn = !!loadStoredAuth();
const returnTo = window.location.pathname + window.location.search;
let generation = 0;
async function load(page: number) {
  if (!loggedIn) return;
  const request = ++generation;
  loading.value = true; error.value = '';
  try {
    const result = await (props.kind === 'financial' ? fetchFinancialPage : fetchResearchPage)(props.reportId, page);
    if (request === generation) { data.value = result; requestedPage.value = result.page; }
  } catch (e) { if (request === generation) error.value = e instanceof Error ? e.message : '正文加载失败，请重试。'; }
  finally { if (request === generation) loading.value = false; }
}
function jump() {
  const page = Math.trunc(Number(requestedPage.value));
  if (Number.isFinite(page) && page >= 1 && page <= (data.value?.ocrPageCount || 1)) void load(page);
}
watch(() => [props.reportId, props.kind], () => { data.value = null; requestedPage.value = 1; void load(1); }, { immediate: true });
onBeforeUnmount(() => { generation++; });
</script>
<template>
  <section class="research-reader">
    <h3>{{ kind === 'financial' ? '财报正文' : '研报正文' }}</h3>
    <p>以下为本地 PDF 的{{ data?.contentSource === 'pdf_text' ? '内嵌文字提取' : '已有 OCR 转写' }}，可能有识别或排版误差；不代表 AI 分析或人工审核结论。</p>
    <p v-if="!loggedIn">登录后可阅读正文。<router-link :to="{path: '/login', query: {redirect: returnTo}}">前往登录</router-link></p>
    <p v-else-if="error" role="alert">{{ error }} <button @click="load(data?.page || 1)">重试</button></p>
    <p v-else-if="loading" role="status">正在加载正文…</p>
    <template v-else-if="data">
      <p class="source">{{ data.fileName }} · PDF {{ data.pageCount }} 页 · {{ data.contentSource === 'pdf_text' ? '文字' : 'OCR' }} {{ data.ocrPageCount }} 页</p>
      <p v-if="data.notes">{{ data.notes }}</p>
      <form v-if="data.ocrAvailable" class="reader-pages" @submit.prevent="jump">
        <button type="button" :disabled="data.page <= 1" @click="load(data.page - 1)">上一页</button>
        <span>{{ data.contentSource === 'pdf_text' ? '文字' : 'OCR' }} 第 {{ data.page }} / {{ data.ocrPageCount }} 页</span>
        <button type="button" :disabled="data.page >= data.ocrPageCount" @click="load(data.page + 1)">下一页</button>
        <input v-model="requestedPage" type="number" min="1" :max="data.ocrPageCount" aria-label="跳转页码" /><button type="submit">跳转</button>
      </form>
      <p v-if="!data.ocrAvailable">当前没有可阅读的正文。</p>
      <p v-else-if="!data.markdown.trim()">该页未识别到文字。</p>
      <div v-else class="reader-content" v-html="renderOcrMarkdown(data.markdown)" />
    </template>
  </section>
</template>
<style scoped>
.research-reader { margin-top:24px; border-top:1px solid var(--c-border); padding-top:16px; } h3 { margin:0 0 12px; } p { color:var(--c-text-secondary); font-size:var(--fs-sm); line-height:1.7; }.source { overflow-wrap:anywhere; }.reader-pages { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:16px 0; font-size:var(--fs-sm); } button,input { padding:7px 10px; border:1px solid var(--c-border); border-radius:6px; background:var(--c-surface); color:var(--c-text); } button { cursor:pointer; } button:disabled { opacity:.5; cursor:default; } input { width:70px; }.reader-content { line-height:1.8; font-size:var(--fs-sm); overflow-x:auto; overflow-wrap:anywhere; }.reader-content :deep(table) { border-collapse:collapse; min-width:100%; }.reader-content :deep(th),.reader-content :deep(td) { border:1px solid var(--c-border); padding:8px; min-width:70px; }.reader-content :deep(h1) { font-size:21px; }.reader-content :deep(h2) { font-size:19px; } [role=alert] { color:var(--c-danger); }
</style>
