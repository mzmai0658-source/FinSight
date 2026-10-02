<script setup lang="ts">
import { computed, nextTick, watch } from "vue";

import type { MessageView } from "@/components/chat/messageView";
import ThinkingTrail from "@/components/chat/ThinkingTrail.vue";
import { defineAsyncComponent } from "vue";
const ChartCard = defineAsyncComponent(() => import("@/components/chat/ChartCard.vue"));
import ReferenceCards from "@/components/chat/ReferenceCards.vue";
import VerificationBadge from "@/components/chat/VerificationBadge.vue";
import FactCard from "@/components/chat/FactCard.vue";
import { fieldLabel, displayCell } from "@/utils/financialDisplay";
import BaseEmpty from "@/components/ui/BaseEmpty.vue";
import ResponsivePanel from "@/components/layout/ResponsivePanel.vue";
import { useMediaQuery } from "@/composables/useMediaQuery";
import { IconChart, IconCheck, IconClose, IconCopy, IconDatabase, IconDocument, IconList } from "@/components/ui/icons";
import { useUiStore, type EvidenceTab } from "@/stores/ui";

const props = defineProps<{
  view: MessageView | null;
}>();

const ui = useUiStore();
const compact = useMediaQuery("(max-width: 1200px)");
// 作品说明：首次进入对话时收起曾记住的桌面证据面板，避免遮挡聊天内容。
if (compact.value) ui.collapseRail();

const tabs = computed(() => [
  { key: "verification" as EvidenceTab, label: "核验", icon: IconCheck, count: (['conversation', 'catalog'].includes(props.view?.responseKind ?? '')) ? 0 : (props.view?.verification?.checks.length ?? 0) },
  { key: "sql" as EvidenceTab, label: "SQL", icon: IconDatabase, count: props.view?.sqlEvidence.length ?? 0 },
  { key: "execution" as EvidenceTab, label: "执行", icon: IconList, count: props.view?.steps.length ?? 0 },
  { key: "chart" as EvidenceTab, label: "图表", icon: IconChart, count: props.view?.charts.length ?? 0 },
  { key: "refs" as EvidenceTab, label: "引用", icon: IconDocument, count: props.view?.references.length ?? 0 },
]);

watch(() => [ui.focusedQueryId, ui.railTab, props.view], async () => {
  await nextTick();
  if (ui.railTab === "sql" && ui.focusedQueryId) {
    document.getElementById(`evidence-query-${ui.focusedQueryId}`)?.scrollIntoView?.({ block: "nearest" });
  }
});

const sqlStatusLabels: Record<string, string> = { success: "已执行", rejected: "已拦截", error: "执行失败" };

function sqlStatus(status?: string) {
  if (!status) return { label: "历史查询", tone: "muted" };
  const tone = status === "success" ? "ok" : status === "rejected" ? "warn" : status === "error" ? "fail" : "muted";
  return { label: sqlStatusLabels[status] ?? status, tone };
}

/** 作品说明：SQL 概览：让“多次尝试、部分被拦截”一眼可见 */
const sqlSummary = computed(() => {
  const list = props.view?.sqlEvidence ?? [];
  return {
    total: list.length,
    success: list.filter((q) => q.status === "success").length,
    rejected: list.filter((q) => q.status === "rejected").length,
    failed: list.filter((q) => q.status === "error").length,
  };
});

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    /* 作品说明：剪贴板不可用时静默 */
  }
}
</script>

<template>
  <ResponsivePanel :overlay="compact" :open="ui.railExpanded" side="right" label="回答证据" @close="ui.collapseRail()">
    <aside class="rail" :class="{ 'rail--expanded': ui.railExpanded }" aria-label="回答证据">
    <!-- 作品说明：收起态：图标条 -->
    <div v-if="!ui.railExpanded" class="rail__strip">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="rail__strip-btn"
        type="button"
        :title="tab.label"
        @click="ui.openRail(tab.key)"
      >
        <component :is="tab.icon" :size="17" />
        <span v-if="tab.count" class="rail__badge">{{ tab.count }}</span>
      </button>
    </div>

    <!-- 作品说明：展开态：证据面板 -->
    <div v-else class="rail__panel">
      <div class="rail__caption">
        <div><strong>回答证据</strong><p>{{ view ? (view.messageIndex < 0 ? '当前回答' : `第 ${Math.floor(view.messageIndex / 2) + 1} 轮回答`) : '提问后可核对数字与原文来源' }}</p></div>
        <button class="rail__close" type="button" title="收起" aria-label="关闭证据面板" @click="ui.collapseRail()"><IconClose :size="15" /></button>
      </div>
      <header class="rail__head">
        <nav class="rail__tabs" aria-label="证据类型">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            class="rail__tab"
            :class="{ 'rail__tab--active': ui.railTab === tab.key }"
            type="button"
            :aria-pressed="ui.railTab === tab.key"
            @click="ui.railTab = tab.key"
          >
            {{ tab.label }}
            <span v-if="tab.count" class="rail__tab-count">{{ tab.count }}</span>
          </button>
        </nav>
      </header>

      <div class="rail__content">
        <template v-if="!view">
          <BaseEmpty text="发起提问后，这里会展示证据详情" />
        </template>

        <!-- 作品说明：自动核验 -->
        <template v-else-if="ui.railTab === 'verification' && ['conversation', 'catalog'].includes(view.responseKind ?? '')">
          <BaseEmpty :text="view.responseKind === 'catalog' ? '本轮查询资料覆盖范围，可在 SQL 页查看目录依据；不进行财务数值核验。' : '本轮是使用说明或对话回应，没有执行财务数据查询。'" />
        </template>
        <template v-else-if="ui.railTab === 'verification'">
          <VerificationBadge :verification="view.verification" />
          <p v-if="view.verification" class="rail__scope">核验范围：{{ view.verification.scope || '仅核对已登记证据；自然语言解释和研究结论仍需人工复核。' }}</p>
          <p v-for="reason in view.verification?.unverified_reasons || []" :key="reason" class="rail__scope">{{ reason }}</p>
          <BaseEmpty v-if="!view.verification" text="该回答尚未经过自动核验" />
          <div v-else class="rail__checks">
            <article
              v-for="check in view.verification.checks"
              :key="check.name"
              class="rail__check"
              :class="`rail__check--${check.status}`"
            >
              <div class="rail__check-head">
                <strong>{{ check.label }}</strong>
                <span>{{ check.status === 'pass' ? '通过' : check.status === 'warn' ? '警告' : '失败' }}</span>
              </div>
              <p>{{ check.detail }}</p>
            </article>
            <div v-if="view.verification.unmatched_numbers.length" class="rail__unmatched">
              <strong>未匹配数字</strong>
              <span v-for="(item, idx) in view.verification.unmatched_numbers" :key="idx">
                {{ item.raw ?? `${item.value ?? ''}${item.unit ?? ''}` }}
              </span>
            </div>
          </div>
        </template>

        <!-- 作品说明：SQL -->
        <template v-else-if="ui.railTab === 'sql'">
          <BaseEmpty v-if="!view.sqlEvidence.length" text="本轮没有执行 SQL" />
          <div v-else class="rail__summary">
            <span>共 <strong>{{ sqlSummary.total }}</strong> 次查询</span>
            <span class="rail__summary-ok">已执行 {{ sqlSummary.success }}</span>
            <span v-if="sqlSummary.rejected" class="rail__summary-warn">已拦截 {{ sqlSummary.rejected }}</span>
            <span v-if="sqlSummary.failed" class="rail__summary-fail">失败 {{ sqlSummary.failed }}</span>
          </div>
          <p v-if="sqlSummary.rejected" class="rail__scope rail__scope--tight">被拦截的查询与问题口径不一致或超出指定公司/报告期，结果不会用于回答。</p>
          <article v-for="query in view.sqlEvidence" :id="`evidence-query-${query.id}`" :key="query.id" class="rail__sql" :class="{ 'rail__sql--focused': ui.focusedQueryId === query.id }">
            <div class="rail__sql-head">
              <span class="rail__sql-id">{{ query.id }}</span>
              <span class="rail__status" :class="`rail__status--${sqlStatus(query.status).tone}`">{{ sqlStatus(query.status).label }}</span>
              <span class="rail__sql-rows">{{ query.row_count ?? query.rows?.length ?? '未知' }} 行</span>
              <button class="rail__icon-btn" type="button" title="复制 SQL" @click="copyText(query.sql || '')">
                <IconCopy :size="13" />
              </button>
            </div>
            <details class="rail__sql-body" :open="query.status !== 'rejected' || ui.focusedQueryId === query.id">
              <summary>SQL 原文</summary>
              <pre class="rail__code">{{ query.sql }}</pre>
            </details>
            <p v-if="!query.rows?.length && query.status !== 'rejected'" class="rail__scope rail__scope--pad">{{ query.row_count === 0 ? '查询未返回记录' : '该查询没有可展示的行证据' }}</p>
            <div v-if="query.rows?.length" class="rail__table-wrap">
              <table class="rail__table">
                <thead>
                  <tr>
                    <th v-for="col in query.columns?.length ? query.columns : Object.keys(query.rows[0])" :key="col">
                      {{ fieldLabel(col) }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, idx) in query.rows" :key="idx">
                    <td v-for="col in query.columns?.length ? query.columns : Object.keys(query.rows[0])" :key="col">
                      {{ displayCell(col, row[col]) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <details v-if="view.facts.some((fact) => fact.query_id === query.id)" class="rail__scope rail__scope--pad">
              <summary>查看字段事实与口径</summary>
              <FactCard v-for="fact in view.facts.filter((fact) => fact.query_id === query.id)" :key="fact.fact_id" :fact="fact" :company="String(query.rows?.find(row => row.row_id === fact.row_id)?.stock_abbr || '')" :live="view.live" />
            </details>
          </article>
        </template>

        <!-- 作品说明：执行过程 -->
        <template v-else-if="ui.railTab === 'execution'">
          <BaseEmpty v-if="!view.steps.length" text="暂无执行记录" />
          <ThinkingTrail v-else :steps="view.steps" :live="view.live" />
        </template>

        <!-- 作品说明：图表 -->
        <template v-else-if="ui.railTab === 'chart'">
          <BaseEmpty v-if="!view.charts.length" text="本轮没有生成图表" />
          <ChartCard
            v-for="(chart, idx) in view.charts"
            :key="idx"
            :chart-data="chart.chartData"
            :image-url="chart.url"
            :title="chart.title"
            :live="view.live"
            @open-source="(queryId) => ui.openRail('sql', view?.messageIndex, queryId)"
          />
        </template>

        <!-- 作品说明：引用 -->
        <template v-else>
          <BaseEmpty v-if="!view.references.length" text="本轮没有引用研报/年报" />
          <ReferenceCards v-else :references="view.references" :live="view.live" />
        </template>
      </div>
    </div>
    </aside>
  </ResponsivePanel>
</template>

<style scoped>
.rail__scope { margin: 10px 0; font-size: var(--fs-xs); line-height: 1.7; color: var(--c-text-secondary); overflow-wrap: anywhere; }
.rail__fact { margin: 8px 0; }
.rail__sql--focused { outline: 2px solid var(--c-primary); outline-offset: 3px; border-radius: var(--r-sm); }
.rail {
  flex-shrink: 0;
  width: var(--rail-right-collapsed);
  border-left: 1px solid var(--c-border);
  background: var(--c-surface);
  transition: width var(--t-base);
  height: 100%;
  overflow: hidden;
}

.rail--expanded {
  width: var(--rail-right-expanded);
}

.rail__strip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-2);
  padding-top: var(--sp-4);
}

.rail__strip-btn {
  position: relative;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--r-md);
  background: none;
  color: var(--c-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all var(--t-fast);
}

.rail__strip-btn:hover {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.rail__badge {
  position: absolute;
  top: 1px;
  right: 1px;
  min-width: 14px;
  height: 14px;
  padding: 0 3px;
  border-radius: var(--r-full);
  background: var(--c-primary);
  color: #fff;
  font-size: 10px;
  line-height: 14px;
  text-align: center;
}

.rail__panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: var(--rail-right-expanded);
}

.rail__head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}
.rail__caption { display: flex; flex: 0 0 auto; align-items: center; justify-content: space-between; padding: var(--sp-4) var(--sp-3) var(--sp-2); }
.rail__caption strong { color: var(--c-ink); font-size: var(--fs-md); }
.rail__caption p { font-size: var(--fs-xs); color: var(--c-text-secondary); margin-top: var(--sp-1); }

.rail__tabs {
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 2px;
}

.rail__tab {
  min-width: 0;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 30px;
  padding: 0 4px;
  border: none;
  border-radius: var(--r-sm);
  background: none;
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: all var(--t-fast);
}

.rail__tab:hover {
  color: var(--c-primary);
}

.rail__tab--active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 500;
}

.rail__tab-count {
  font-size: 10px;
  background: var(--c-border);
  border-radius: var(--r-full);
  padding: 0 5px;
  line-height: 14px;
  color: var(--c-text-secondary);
}

.rail__tab--active .rail__tab-count {
  background: var(--c-primary);
  color: #fff;
}

.rail__close {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--r-sm);
  background: none;
  color: var(--c-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.rail__close:hover {
  background: var(--c-bg);
  color: var(--c-text);
}

.rail__content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

/* 作品说明：纵向 flex 容器里的卡片不能被压缩：overflow:hidden 的子项最小高度会变成 0，被挤扁后看起来互相遮挡 */
.rail__content > * {
  flex-shrink: 0;
}

.rail__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2) var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
  border: 1px solid var(--c-border);
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
}

.rail__summary strong {
  color: var(--c-text);
}

.rail__summary-ok { color: var(--c-success); }
.rail__summary-warn { color: var(--c-warning); }
.rail__summary-fail { color: var(--c-danger); }

.rail__scope--tight {
  margin: calc(-1 * var(--sp-1)) 0 0;
}

.rail__scope--pad {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
}

.rail__checks {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.rail__check {
  padding: var(--sp-3);
  border: 1px solid var(--c-border);
  border-left-width: 3px;
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
}

.rail__check--pass { border-left-color: var(--c-success); }
.rail__check--warn { border-left-color: var(--c-warning); }
.rail__check--fail { border-left-color: var(--c-danger); }

.rail__check-head {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
}

.rail__check-head span {
  color: var(--c-text-secondary);
  font-size: var(--fs-xs);
}

.rail__check p {
  margin: var(--sp-1) 0 0;
  color: var(--c-text-secondary);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.rail__unmatched {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-xs);
}

.rail__unmatched strong {
  flex-basis: 100%;
}

.rail__sql {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  overflow: hidden;
  background: var(--c-surface);
}

.rail__sql-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-1) var(--sp-2) var(--sp-1) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
  background: var(--c-surface-muted);
  border-bottom: 1px solid var(--c-border);
}

.rail__sql-id {
  font-family: var(--font-mono);
  color: var(--c-text);
}

.rail__sql-rows {
  margin-left: auto;
}

.rail__status {
  padding: 1px 8px;
  border-radius: var(--r-full);
  font-size: 11px;
  font-weight: 500;
}

.rail__status--ok { background: var(--c-success-soft); color: var(--c-success); }
.rail__status--warn { background: var(--c-warning-soft); color: var(--c-warning); }
.rail__status--fail { background: var(--c-danger-soft); color: var(--c-danger); }
.rail__status--muted { background: var(--c-bg); color: var(--c-text-tertiary); }

.rail__sql-body > summary {
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  cursor: pointer;
  user-select: none;
}

.rail__sql-body[open] > summary {
  border-bottom: 1px solid var(--c-border);
}

.rail__icon-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  border-radius: var(--r-sm);
  color: var(--c-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.rail__icon-btn:hover {
  color: var(--c-primary);
  background: var(--c-primary-soft);
}

.rail__code {
  margin: 0;
  padding: var(--sp-3);
  font-size: var(--fs-xs);
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--c-code-bg);
}

.rail__table-wrap {
  overflow-x: auto;
  border-top: 1px solid var(--c-border);
}

.rail__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-xs);
  font-family: var(--font-mono);
}

.rail__table th,
.rail__table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}

.rail__table th {
  background: var(--c-surface-muted);
  color: var(--c-text-secondary);
  font-weight: 500;
}

.rail__table tr:last-child td {
  border-bottom: none;
}

@media (max-width: 1200px) {
  .rail {
    width: 100%;
    height: 100%;
  }

  .rail--expanded {
    width: 100%;
  }

  .rail__panel {
    width: 100%;
  }

  .rail__tabs {
    min-width: 0;
    overflow-x: auto;
  }

  .rail__tab {
    flex: 0 0 auto;
  }
}
</style>
