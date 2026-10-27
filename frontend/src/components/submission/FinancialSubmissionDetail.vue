<script setup lang="ts">
import { ref, watch } from "vue";
import AppBadge from "@/components/ui/AppBadge.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCard from "@/components/ui/AppCard.vue";
import CandidateReviewPanel from "./CandidateReviewPanel.vue";
import SubmissionOriginal from "./SubmissionOriginal.vue";
import type { FinancialSubmission } from "@/types/financialSubmissions";
import { reportPeriod } from "@/utils/financialDisplay";
import { processingLabels, reviewLabels, reviewVariant } from "@/utils/financialSubmission";

const props = defineProps<{ submission: FinancialSubmission; admin?: boolean; busy?: boolean; retrying?: boolean }>();
const emit = defineEmits<{ close: []; retry: []; changed: [submission: FinancialSubmission]; busy: [value: boolean] }>();
const original = ref<InstanceType<typeof SubmissionOriginal> | null>(null);
const originalOpened = ref(false);
watch([() => props.submission.id, () => props.submission.sourceSha256], () => { originalOpened.value = false; });
</script>

<template>
  <AppCard padding="lg" class="submission-detail" aria-label="提交详情">
    <div class="submission-detail__header"><div><span class="submission-detail__eyebrow">提交 #{{ submission.id }}</span><h2>{{ submission.fileName }}</h2></div><AppButton variant="ghost" size="sm" :disabled="busy" @click="emit('close')">关闭详情</AppButton></div>
    <dl class="submission-detail__meta">
      <div><dt>处理状态</dt><dd>{{ processingLabels[submission.status] || submission.status }}</dd></div>
      <div><dt>审核与发布</dt><dd><AppBadge :variant="reviewVariant(submission.reviewStatus)" data-testid="review-status">{{ reviewLabels[submission.reviewStatus] || '状态未登记' }}</AppBadge></dd></div>
      <div><dt>公司</dt><dd>{{ submission.company || '待识别' }}<span v-if="submission.stockCode">（{{ submission.stockCode }}）</span></dd></div>
      <div><dt>报告期</dt><dd>{{ reportPeriod(submission.reportYear, submission.reportPeriod || '') }}</dd></div>
      <div><dt>提交时间</dt><dd>{{ submission.createdAt || '未登记' }}</dd></div>
      <div><dt>发布时间版本</dt><dd>{{ submission.publishedVersion || '尚未发布' }}</dd></div>
    </dl>
    <p class="submission-detail__message"><strong>处理阶段记录：</strong>{{ submission.message || '等待服务端更新处理结果。' }}</p>
    <p class="submission-detail__hint">处理完成后进入候选审核；批准后仍需管理员选定发布，发布前不会进入公开公司目录和问答资料。</p>
    <AppButton v-if="['FAILED', 'PARTIAL'].includes(submission.status) && submission.reviewStatus === 'PROCESSING'" class="submission-detail__retry" variant="outline" :loading="retrying" :disabled="busy" @click="emit('retry')">重试处理</AppButton>
    <SubmissionOriginal :key="`${submission.id}:${submission.sourceSha256 || ''}`" ref="original" :submission-id="submission.id" @opened="originalOpened = true" />
    <CandidateReviewPanel v-if="admin" :submission="submission" :original-opened="originalOpened" @changed="emit('changed', $event)" @busy="emit('busy', $event)" @page="original?.open($event)" />
    <details class="submission-detail__technical"><summary>处理标识</summary><dl><dt>任务编号</dt><dd>{{ submission.taskUid || '未登记' }}</dd><dt>候选编号</dt><dd>{{ submission.candidateId || '尚未生成' }}</dd><dt>原件摘要</dt><dd>{{ submission.sourceSha256 || '尚未生成' }}</dd></dl></details>
  </AppCard>
</template>

<style scoped>
.submission-detail :deep(.app-card__body) { display: grid; gap: var(--sp-5); }
.submission-detail__header { display: flex; align-items: start; justify-content: space-between; gap: var(--sp-3); }
.submission-detail__header > div { min-width: 0; } h2 { font-size: var(--fs-lg); color: var(--c-ink); overflow-wrap: anywhere; margin-top: var(--sp-2); }
.submission-detail__eyebrow { font-size: var(--fs-xs); color: var(--c-primary); }
.submission-detail__meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-4); margin: 0; }
dt { font-size: var(--fs-xs); color: var(--c-text-secondary); margin-bottom: 7px; } dd { margin: 0; font-size: var(--fs-sm); overflow-wrap: anywhere; }
.submission-detail__message { padding: var(--sp-3); border-radius: var(--r-md); background: var(--c-bg); color: var(--c-text); line-height: 1.7; font-size: var(--fs-sm); white-space: pre-wrap; overflow-wrap: anywhere; }
.submission-detail__hint { color: var(--c-text-secondary); font-size: var(--fs-sm); line-height: 1.7; }
.submission-detail__retry { justify-self: start; }
.submission-detail__technical { font-size: var(--fs-sm); color: var(--c-text-secondary); } summary { cursor: pointer; } .submission-detail__technical dl { margin-top: var(--sp-3); }
@media (max-width: 700px) { .submission-detail__meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } .submission-detail { padding: var(--sp-4); } }
@media (max-width: 400px) { .submission-detail__meta { grid-template-columns: 1fr; } }
</style>
