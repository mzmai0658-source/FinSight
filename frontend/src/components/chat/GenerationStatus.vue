<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import type { LiveStep } from '@/stores/session';
import BaseSpinner from '@/components/ui/BaseSpinner.vue';
const props = defineProps<{ steps: LiveStep[]; error?: string; startedAt?: number; holding?: boolean }>();
const mountedAt = Date.now();
const now = ref(Date.now());
const timer = setInterval(() => { now.value = Date.now(); }, 1000);
onBeforeUnmount(() => clearInterval(timer));
const seconds = computed(() => Math.max(0, Math.floor((now.value - (props.startedAt || mountedAt))/1000)));
const label = computed(() => {
  if (props.holding) return '结果仍在保存';
  if (props.error) return '本次请求出现异常';
  const running = [...props.steps].reverse().find(s => s.status === 'running');
  if (running?.tool === 'plan') return '正在理解问题并准备查询';
  if (running?.tool === 'query_database') return '正在查询财报数据';
  if (running?.tool === 'search_documents') return '正在检索原文证据';
  if (running?.tool === 'render_chart') return '正在生成图表';
  return props.steps.length ? '正在等待模型整理回答与核验结果' : '已提交，正在等待模型响应';
});
</script>
<template>
  <section class="generation-status" :class="{ 'generation-status--error': error }" role="status" aria-live="polite">
    <div><BaseSpinner v-if="!error" :size="18" /><strong>{{ label }}</strong><span aria-live="off">已等待 {{ seconds }} 秒</span></div>
    <p v-if="holding">这一轮还在后台完成，保存好之后会显示在这里。请稍候，先不要发送。</p>
    <p v-else-if="error">{{ error }}</p>
    <p v-else>正在查询财报或整理回答，完成后显示。请稍候，无需重复发送。</p>
    <p v-if="!error && !holding && seconds >= 60">等待时间较长，原文引用与解释可能需要几分钟；以上是已等待时间，不是剩余时间。连接异常时会单独提示。</p>
  </section>
</template>
<style scoped>
.generation-status { padding:16px; border:1px solid var(--c-primary-border); background:var(--c-primary-soft); border-radius:12px; }
.generation-status > div { display:flex; align-items:center; flex-wrap:wrap; gap:10px; } strong { font-size:var(--fs-sm); } span,p { font-size:var(--fs-xs); color:var(--c-text-secondary); } p { margin:8px 0 0; line-height:1.7; }.generation-status--error { border-color:var(--c-danger); }
</style>
