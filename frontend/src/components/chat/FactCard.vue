<script setup lang="ts">
import type { EvidenceFact } from '@/types/api';
import { fieldLabel, reportPeriod, fileName } from '@/utils/financialDisplay';
defineProps<{ fact: EvidenceFact; company?: string; live?: boolean }>();
</script>
<template>
  <section class="fact-card">
    <dl>
      <dt>公司</dt><dd>{{ fact.stock_abbr || company || '公司名称未登记' }}<span v-if="fact.stock_code">（{{ fact.stock_code }}）</span></dd>
      <dt>报告期</dt><dd>{{ reportPeriod(fact.report_year, fact.report_period) }}</dd>
      <dt>指标</dt><dd>{{ fact.label || fieldLabel(fact.field) }}</dd>
      <template v-if="fact.scope"><dt>报表范围</dt><dd>{{ fact.scope === 'parent' ? '母公司' : '合并' }}</dd></template>
      <template v-if="fact.field === 'roe' || fact.field === 'roe_weighted_excl_non_recurring'"><dt>口径</dt><dd>{{ fact.field === 'roe' ? '普通口径，未扣除非经常性损益。' : '扣非口径，已扣除非经常性损益；与普通口径分别展示。' }}</dd></template>
      <dt>数值</dt><dd><strong>{{ fact.value_exact ?? fact.value ?? '暂无核实数据' }}{{ fact.value != null ? fact.unit : '' }}</strong></dd>
      <dt>来源</dt><dd>{{ fileName(fact.source?.source_path || fact.source?.path || fact.source?.title) }}</dd>
      <dt>原件页码</dt><dd>{{ fact.source?.page_start ? `第 ${fact.source.page_start}${fact.source.page_end && fact.source.page_end !== fact.source.page_start ? `–${fact.source.page_end}` : ''} 页` : '尚未定位' }}</dd>
    </dl>
    <p v-if="!fact.source?.page_start" data-source-status="source_unlocated">原文位置未登记，当前仅能回查数据库行。</p>
    <details><summary>技术详情</summary><dl><dt>字段</dt><dd>{{ fact.field }}</dd><dt>证据编号</dt><dd>{{ fact.fact_id }}</dd><dt>数据行编号</dt><dd>{{ fact.row_id }}</dd><dt>查询编号</dt><dd>{{ fact.query_id }}</dd></dl></details>
  </section>
</template>
<style scoped>
.fact-card { margin: 12px 0; padding: 12px; border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-surface); color: var(--c-text); }
dl { display:grid; grid-template-columns: 64px minmax(0,1fr); gap: 7px 10px; margin:0 0 12px; font-size:var(--fs-sm); }
dt { color:var(--c-text-secondary); } dd { margin:0; overflow-wrap:anywhere; } details { margin-top:12px; color:var(--c-text-secondary); } summary { cursor:pointer; } details dl { margin-top:8px; font-size:var(--fs-xs); } p { font-size:var(--fs-xs); }
</style>
