<script setup lang="ts">
import { computed } from "vue";
import type { MessageView } from "./messageView";
import { renderMarkdown } from "@/utils/markdown";
import ThinkingTrail from "./ThinkingTrail.vue";
import GenerationStatus from "./GenerationStatus.vue";
import SqlCard from "./SqlCard.vue";
import { defineAsyncComponent } from "vue";
const ChartCard = defineAsyncComponent(() => import("./ChartCard.vue"));
import ReferenceCards from "./ReferenceCards.vue";
import ClarifyOptions from "./ClarifyOptions.vue";
import VerificationBadge from "./VerificationBadge.vue";
import RegisteredAsset from "./RegisteredAsset.vue";
import { IconAlert, IconSpark } from "@/components/ui/icons";
import { useUiStore, type EvidenceTab } from "@/stores/ui";
import { periods } from "@/utils/financialDisplay";
const ui = useUiStore();
const props = defineProps<{
  view: MessageView;
  clarifyDisabled?: boolean;
}>();
const sourceLinks = computed(() => {
  const seen = new Set<string>();
  const links: Array<{ key: string; stockCode: string; year: number; period: string; page: number; label: string }> = [];
  for (const fact of props.view.facts) {
    const page = fact.source?.page_start;
    const code = fact.stock_code;
    const year = Number(fact.report_year);
    const period = fact.report_period;
    if (!page || !code || !year || !period) continue;
    const start = Number(page);
    const end = Number(fact.source?.page_end);
    const key = `${code}|${year}|${period}|${start}|${end || start}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const periodLabel = periods[period] || period;
    const pageLabel = end && end !== start ? `第${start}–${end}页` : `第${start}页`;
    const company = fact.stock_abbr ? `${fact.stock_abbr}` : "";
    const label = `打开${company}${year}年${periodLabel}${pageLabel}`;
    links.push({ key, stockCode: code, year, period, page: start, label });
  }
  return links;
});
const outcomeLabels = {
  answered: "已完成", partial: "部分结果可用", no_data: "没有可用数据",
  query_failed: "本轮查询失败", needs_clarification: "需要补充条件", unsupported: "暂不支持该请求",
  cancelled: "已取消", failed: "本轮失败",
};

defineEmits<{
  openDetail: [tab: EvidenceTab, messageIndex: number];
  selectClarify: [option: string];
  retry: [messageIndex: number];
}>();
</script>

<template>
  <article class="msg" :class="`msg--${view.role}`">
    <div v-if="view.role === 'assistant'" class="msg__avatar">
      <IconSpark :size="15" />
    </div>

    <div class="msg__main">
      <!-- 作品说明：用户消息 -->
      <div v-if="view.role === 'user'" class="msg__bubble msg__bubble--user">{{ view.content }}</div>

      <!-- 作品说明：助手消息：执行轨迹 + 证据卡片 + 正文 -->
      <div v-else class="msg__stack">
        <span v-if="view.datasetProfile?.kind === 'synthetic'" class="msg__outcome" role="note">虚构演示数据 · 仅用于功能验证</span>
        <GenerationStatus v-if="view.live && !view.needsClarification" :steps="view.steps" :error="view.error" :started-at="view.startedAt" :holding="view.holding" />
        <span v-if="view.outcome" class="msg__outcome" :class="`msg__outcome--${view.outcome.status}`" role="status">{{ outcomeLabels[view.outcome.status] }}</span>
        <div v-if="view.content" class="msg__bubble msg__bubble--assistant" :class="{ 'msg__bubble--clarify': view.needsClarification }">
          <!-- eslint-disable-next-line vue/no-v-html — 内容已在 renderMarkdown 内转义原始 HTML -->
          <div class="msg__content" v-html="renderMarkdown(view.content)" /><span v-if="view.live && !view.needsClarification" class="msg__cursor" />
        </div>
        <div v-if="sourceLinks.length" class="msg__sources">
          <RegisteredAsset
            v-for="link in sourceLinks"
            :key="link.key"
            :stock-code="link.stockCode"
            :report-year="link.year"
            :report-period="link.period"
            :page="link.page"
            :disabled="view.live"
            :label="link.label"
          />
        </div>
        <span v-if="view.answerAssessment && !view.answerAssessment.accepted && view.facts.length" class="msg__answer-note">已保留可核对数据，完整解释暂未完成</span>

        <ClarifyOptions
          v-if="view.needsClarification && view.clarifyOptions.length"
          :options="view.clarifyOptions"
          :disabled="clarifyDisabled"
          @select="$emit('selectClarify', $event)"
        />

        <ChartCard
          v-for="(chart, idx) in view.charts"
          :key="idx"
          :chart-data="chart.chartData"
          :image-url="chart.url"
          :title="chart.title"
          :live="view.live"
          @open-source="(queryId) => ui.openRail('sql', view.messageIndex, queryId)"
        />

        <ReferenceCards
          v-if="view.references.length"
          :references="view.references"
          compact
          :live="view.live"
          @open-detail="$emit('openDetail', 'refs', view.messageIndex)"
        />

        <VerificationBadge
          v-if="view.responseKind !== 'conversation' && view.responseKind !== 'catalog' && (!view.outcome || view.facts.length > 0)"
          :verification="view.verification"
          interactive
          @open-detail="$emit('openDetail', 'verification', view.messageIndex)"
        />
        <ThinkingTrail v-if="view.steps.length" :steps="view.steps" :live="view.live" />

        <SqlCard
          v-for="query in view.sqlEvidence"
          :key="query.id"
          :sql="query.sql || ''"
          :query-id="query.id"
          :row-count="query.row_count"
          :rows="query.rows"
          :columns="query.columns"
          @open-detail="ui.openRail('sql', view.messageIndex, query.id)"
        />
        <small v-if="view.diagnosticId && (view.outcome?.status === 'query_failed' || view.answerAssessment?.accepted === false)" class="msg__diagnostic">问题编号：{{ view.diagnosticId }}</small>

        <div v-if="view.error" class="msg__error">
          <IconAlert :size="14" />
          {{ view.error }}
          <button v-if="!view.live" type="button" :disabled="clarifyDisabled" @click="$emit('retry', view.messageIndex)">重试问题</button>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.msg__outcome { align-self: flex-start; padding: 4px 10px; border-radius: 12px; background: #edf3ff; color: #2454b8; font-size: 12px; }
.msg__outcome--query_failed { background: #fff0f0; color: #b42318; }
.msg__answer-note { color: var(--text-muted, #68758b); font-size: 12px; }
.msg__outcome--partial, .msg__outcome--needs_clarification { background: #fff7e6; color: #8a5500; }
.msg {
  display: flex;
  gap: var(--sp-3);
  animation: fade-in-up 0.25s ease;
}

.msg--user {
  justify-content: flex-end;
}

.msg__avatar {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}

.msg__main {
  min-width: 0;
  max-width: 92%;
}

.msg--assistant .msg__main {
  flex: 1;
}

.msg__stack {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.msg__bubble {
  max-width: 100%;
  border-radius: var(--r-lg);
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fs-md);
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.msg__bubble--user {
  background: var(--c-primary);
  color: #fff;
  border-bottom-right-radius: var(--r-sm);
  white-space: pre-wrap;
}

.msg__bubble--assistant {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-top-left-radius: var(--r-sm);
}

.msg__sources {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.msg__bubble--clarify {
  border-color: var(--c-warning);
  background: var(--c-warning-soft);
}

.msg__content {
  display: block;
  min-width: 0;
}

/* 作品说明：Markdown 渲染样式 */
.msg__content :deep(p) {
  margin: 0 0 var(--sp-2);
}

.msg__content :deep(p:last-child) {
  margin-bottom: 0;
}

.msg__content :deep(ul),
.msg__content :deep(ol) {
  margin: var(--sp-1) 0 var(--sp-2);
  padding-left: 22px;
}

.msg__content :deep(li) {
  margin-bottom: 2px;
}

.msg__content :deep(h1),
.msg__content :deep(h2),
.msg__content :deep(h3),
.msg__content :deep(h4) {
  font-size: var(--fs-md);
  font-weight: 600;
  margin: var(--sp-3) 0 var(--sp-2);
}

.msg__content :deep(code) {
  font-size: var(--fs-xs);
  background: var(--c-code-bg);
  border-radius: 4px;
  padding: 1px 5px;
}

.msg__content :deep(pre) {
  background: var(--c-code-bg);
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
  overflow-x: auto;
  margin: var(--sp-2) 0;
}

.msg__content :deep(pre code) {
  background: none;
  padding: 0;
}

.msg__content :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  margin: var(--sp-2) 0;
  font-size: var(--fs-sm);
  width: 100%;
  white-space: nowrap;
}

.msg__content :deep(th),
.msg__content :deep(td) {
  border: 1px solid var(--c-border);
  padding: 5px 10px;
  text-align: left;
}

.msg__content :deep(th) {
  background: var(--c-surface-muted);
  font-weight: 500;
}

.msg__content :deep(blockquote) {
  margin: var(--sp-2) 0;
  padding: var(--sp-1) var(--sp-3);
  border-left: 3px solid var(--c-primary-border);
  color: var(--c-text-secondary);
}

.msg__content :deep(hr) {
  border: none;
  border-top: 1px solid var(--c-border);
  margin: var(--sp-3) 0;
}

.msg__cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--c-primary);
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 0.9s step-end infinite;
}

.msg__error {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}
</style>
