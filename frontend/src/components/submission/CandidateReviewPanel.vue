<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import AppBadge from "@/components/ui/AppBadge.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppTable from "@/components/ui/AppTable.vue";
import { fetchFinancialSubmissionCandidate, reviewFinancialSubmission } from "@/services/api";
import type { FinancialCandidate, FinancialIdentity, FinancialSubmission } from "@/types/financialSubmissions";
import { fieldLabel, fileName, reportPeriod } from "@/utils/financialDisplay";
import { displayEvidence } from "@/utils/financialSubmission";

const props = defineProps<{ submission: FinancialSubmission; originalOpened: boolean }>();
const emit = defineEmits<{ changed: [submission: FinancialSubmission]; busy: [value: boolean]; page: [value: number] }>();
const candidate = ref<FinancialCandidate | null>(null);
const loading = ref(false);
const error = ref("");
const reviewError = ref("");
const reviewing = ref(false);
const confirmed = ref(false);
const reason = ref("");
const aliases = ref("");
const identity = reactive({ stock_code: "", company: "", year: "", period: "" });
let generation = 0;

async function loadCandidate() {
  const request = ++generation;
  candidate.value = null;
  error.value = "";
  confirmed.value = false;
  reason.value = "";
  if (!props.submission.candidateId) return;
  loading.value = true;
  try {
    const result = await fetchFinancialSubmissionCandidate(props.submission.id);
    if (request !== generation) return;
    candidate.value = result;
    identity.stock_code = result.identity?.stock_code || props.submission.stockCode || "";
    identity.company = result.identity?.company || props.submission.company || "";
    identity.year = String(result.identity?.year ?? props.submission.reportYear ?? "");
    identity.period = result.identity?.period || props.submission.reportPeriod || "";
    aliases.value = Array.isArray(result.identity?.aliases) ? result.identity.aliases.join("，") : "";
  } catch (cause) {
    if (request === generation) error.value = cause instanceof Error ? cause.message : "候选详情加载失败。";
  } finally {
    if (request === generation) loading.value = false;
  }
}

watch([() => props.submission.id, () => props.submission.candidateId, () => props.submission.sourceSha256, () => props.submission.status, () => props.submission.reviewStatus], () => { reviewError.value = ""; void loadCandidate(); }, { immediate: true });
watch(() => props.originalOpened, () => { confirmed.value = false; });
watch(() => [identity.stock_code, identity.company, identity.year, identity.period, aliases.value], () => { confirmed.value = false; }, { flush: "sync" });
onBeforeUnmount(() => { generation += 1; });

const qualityChecks = computed(() => [
  { name: "公司与报告身份", value: candidate.value?.quality?.identity_verified },
  { name: "数字来源核验", value: candidate.value?.quality?.facts_verified },
  { name: "报告核验", value: candidate.value?.quality?.reports_verified },
  { name: "索引页核验", value: candidate.value?.quality?.index_verified },
]);
const approvalBlock = computed(() => {
  const quality = candidate.value?.quality;
  const conflicts = quality?.conflicts;
  const conflictCount = Array.isArray(conflicts) ? conflicts.length : conflicts && typeof conflicts === "object" ? Object.keys(conflicts).length : Number(conflicts) || (conflicts ? 1 : 0);
  if (conflictCount > 0) return "存在未解决冲突，不能批准。";
  if (quality?.verified_count === 0) return "尚无已核验事实，不能批准。";
  if (qualityChecks.value.some(check => check.value === false)) return "存在未通过的质量核验，需先解决后再批准。";
  if (qualityChecks.value.some(check => check.value !== true)) return "质量核验尚未完成，不能批准。";
  return "";
});
const identityValid = computed(() => /^\d{6}$/.test(identity.stock_code.trim()) && Boolean(identity.company.trim()) && /^\d{4}$/.test(identity.year) && Number(identity.year) >= 1900 && ["FY", "HY", "Q1", "Q3"].includes(identity.period));
const waiting = computed(() => props.submission.reviewStatus === "WAITING");
const canApprove = computed(() => waiting.value && Boolean(candidate.value) && props.originalOpened && confirmed.value && identityValid.value && !approvalBlock.value && !reviewing.value);

async function review(decision: "approve" | "reject") {
  if (reviewing.value || !waiting.value || (decision === "approve" ? !canApprove.value : !reason.value.trim())) return;
  reviewing.value = true;
  reviewError.value = "";
  emit("busy", true);
  try {
    const approvedIdentity: FinancialIdentity = {
      stock_code: identity.stock_code.trim(), company: identity.company.trim(), year: Number(identity.year), period: identity.period,
      aliases: [...new Set(aliases.value.split(/[,，;；\n]+/).map(value => value.trim()).filter(Boolean))],
    };
    const result = await reviewFinancialSubmission(props.submission.id, { decision, reason: reason.value.trim(), ...(decision === "approve" ? { identity: approvedIdentity } : {}) });
    emit("changed", result);
  } catch (cause) {
    reviewError.value = cause instanceof Error ? cause.message : "审核未保存，请重试。";
  } finally {
    reviewing.value = false;
    emit("busy", false);
  }
}

const factStatus: Record<string, string> = { verified: "已核验", derived: "已核验派生值", unverified: "尚未核验", conflict: "来源冲突", scope_unknown: "报表范围待核验" };
const sourceStatus: Record<string, string> = { literal_checked: "已核对原文", human_checked: "已人工核验", unverified: "尚未核验" };
const excludedStatus: Record<string, string> = { not_disclosed: "未披露", unrecognized: "无法识别", unverified: "待核验", conflict: "冲突", conflicts: "冲突", missing: "未提取，原因待核查" };
const facts = computed(() => Array.isArray(candidate.value?.facts) ? candidate.value.facts : []);
const exclusions = computed(() => {
  const raw = candidate.value?.excluded;
  const asRecord = (value: unknown, key: string): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? { metric: key, ...value } : { metric: key, reason: value };
  if (Array.isArray(raw)) return raw.map((value, index) => asRecord(value, String(index + 1)));
  if (!raw || typeof raw !== "object") return [];
  return Object.entries(raw).flatMap(([key, value]) => Array.isArray(value) ? value.map(item => ({ ...asRecord(item, ""), status: key })) : [asRecord(value, key)]);
});
</script>

<template>
  <section class="candidate" aria-label="候选事实与审核">
    <div class="candidate__heading"><h3>候选事实与审核</h3><span v-if="candidate">原件 {{ candidate.page_count ?? '页数未登记' }} 页 · 已核验 {{ candidate.quality?.verified_count ?? '未登记' }} 条 · 已排除 {{ candidate.quality?.excluded_count ?? exclusions.length }} 条</span></div>
    <p v-if="!submission.candidateId" class="candidate__hint">处理尚未生成候选，请等待处理状态更新。</p>
    <p v-if="loading" role="status">正在加载候选证据…</p>
    <p v-if="error" class="candidate__error" role="alert">{{ error }} <AppButton size="sm" variant="outline" @click="loadCandidate">重新加载候选</AppButton></p>
    <template v-if="candidate">
      <div class="candidate__quality">
        <div v-for="check in qualityChecks" :key="check.name"><span>{{ check.name }}</span><AppBadge :variant="check.value === true ? 'success' : 'warning'">{{ check.value === true ? '已核验' : check.value === false ? '未通过' : '未核验' }}</AppBadge></div>
      </div>
      <p class="candidate__hint">候选数字只供审核。缺失、待核验或冲突的数据不会因处理完成而成为已发布事实。</p>
      <p v-if="approvalBlock" class="candidate__error">{{ approvalBlock }}</p>
      <details v-if="candidate.quality"><summary>查看质量核验详情</summary><pre>{{ displayEvidence(candidate.quality) }}</pre></details>
      <ol v-if="candidate.steps?.length" class="candidate__steps"><li v-for="(step, index) in candidate.steps" :key="index"><strong>{{ step.name }}</strong> · {{ step.status }}<span v-if="step.message"> · {{ step.message }}</span></li></ol>

      <form v-if="waiting" class="candidate__form" @submit.prevent="review('approve')">
        <h4>核对并确认报告身份</h4>
        <p class="candidate__hint">请对照原件确认。身份与候选事实不一致时需重新处理，不能通过修改名称或报告期给事实重新归属。</p>
        <fieldset :disabled="reviewing"><legend class="sr-only">报告身份</legend>
          <label>股票代码<input v-model="identity.stock_code" aria-label="股票代码" inputmode="numeric" maxlength="6" autocomplete="off" /></label>
          <label>公司名称<input v-model="identity.company" aria-label="公司名称" autocomplete="off" /></label>
          <label>报告年份<input v-model="identity.year" aria-label="报告年份" inputmode="numeric" maxlength="4" /></label>
          <label>报告期<select v-model="identity.period" aria-label="报告期"><option value="">请选择</option><option value="FY">全年 FY</option><option value="HY">上半年 HY</option><option value="Q1">第一季度 Q1</option><option value="Q3">前三季度 Q3</option></select></label>
          <label class="candidate__wide">公司别名<input v-model="aliases" aria-label="公司别名" placeholder="多个别名用逗号分隔；没有可留空" /></label>
        </fieldset>
        <label class="candidate__confirmation"><input v-model="confirmed" type="checkbox" aria-label="确认已人工核对原件身份" :disabled="!originalOpened || reviewing" />我已人工对照原始 PDF 核对公司、报告期与上述别名。</label>
        <p v-if="!originalOpened" class="candidate__hint">请先打开原始 PDF，再完成人工核对和确认。</p>
        <p v-if="!identityValid" class="candidate__hint">批准前需填写六位股票代码、公司名称、有效年份与报告期。</p>
        <label class="candidate__reason">审核原因（拒绝时必填）<textarea v-model="reason" aria-label="审核原因" rows="3" :disabled="reviewing" placeholder="记录问题或本次核对说明" /></label>
        <p v-if="reviewError" class="candidate__error" role="alert">{{ reviewError }}</p>
        <div class="candidate__actions"><AppButton type="submit" :disabled="!canApprove" :loading="reviewing">批准，等待发布</AppButton><AppButton variant="danger" :disabled="!reason.trim() || reviewing" @click="review('reject')">拒绝</AppButton><span>批准后仍需在列表中选中并发布。</span></div>
      </form>
      <div v-else-if="candidate.review" class="candidate__review"><h4>已保存的审核记录</h4><pre>{{ displayEvidence(candidate.review) }}</pre></div>

      <h4>候选事实（{{ facts.length }}）</h4>
      <p v-if="!facts.length" class="candidate__hint">没有可展示的候选事实，不能将缺失数字视为零。</p>
      <AppTable v-else>
        <thead><tr><th>指标与报告期</th><th>候选值与状态</th><th>原文位置</th><th>原始单元格与核验</th></tr></thead>
        <tbody><tr v-for="(fact, index) in facts" :key="fact.id || index">
          <td><strong>{{ fieldLabel(fact.metric || String(fact.field || '')) }}</strong><small>{{ fact.metric || fact.field || '指标未登记' }}</small><small>{{ reportPeriod(fact.year, fact.period) }} · {{ fact.scope === 'parent' ? '母公司' : fact.scope === 'consolidated' ? '合并' : '范围待核验' }}</small></td>
          <td><strong>{{ fact.value ?? '未披露或未识别' }}<template v-if="fact.value != null"> {{ fact.unit || '单位未登记' }}</template></strong><small>{{ factStatus[fact.status || ''] || '尚未核验' }}</small></td>
          <td><template v-if="fact.source"><span>{{ fact.source.table || '表名未登记' }}</span><small>{{ fact.source.row || '行名未登记' }} · {{ fact.source.column || '列名未登记' }}</small><small>{{ fileName(fact.source.source_path || submission.fileName) }}</small></template><button v-if="fact.source?.page && Number.isInteger(fact.source.page) && fact.source.page > 0" type="button" class="candidate__link" @click="emit('page', fact.source.page)">查看第 {{ fact.source.page }} 页</button><span v-else class="candidate__missing">来源位置未登记</span></td>
          <td><span>原值：{{ fact.source?.raw_value ?? '未登记' }} {{ fact.source?.raw_unit || '' }}</span><small>{{ sourceStatus[fact.source?.verification || ''] || '尚未核验' }}</small><p v-if="fact.source?.literal" class="candidate__literal">{{ fact.source.literal }}</p><details v-if="fact.source?.original_cell || fact.source?.scope_proof"><summary>原始单元格与范围证据</summary><pre>{{ displayEvidence({ original_cell: fact.source.original_cell, scope_proof: fact.source.scope_proof }) }}</pre></details></td>
        </tr></tbody>
      </AppTable>
      <section class="candidate__excluded" aria-label="已排除的数据"><h4>已排除的数据（{{ exclusions.length }}）</h4><p class="candidate__hint">未披露、无法识别、待核验与冲突分别保留原因，不补零。</p><p v-if="!exclusions.length" class="candidate__hint">没有返回排除明细。</p><ul v-else><li v-for="(item, index) in exclusions" :key="index"><strong>{{ item.metric || item.field || '未登记指标' }}</strong> · {{ excludedStatus[String(item.status || item.category || '')] || item.status || item.category || '待核验' }} · {{ displayEvidence(item.reason ?? item.message ?? item.detail) }}</li></ul><details v-if="exclusions.length"><summary>完整排除明细</summary><pre>{{ displayEvidence(candidate.excluded) }}</pre></details></section>
      <details v-if="candidate.index"><summary>候选索引与叙述（{{ candidate.narrative_count ?? '数量未登记' }}）</summary><pre>{{ displayEvidence(candidate.index) }}</pre></details>
    </template>
  </section>
</template>

<style scoped>
.candidate { display: grid; gap: var(--sp-4); min-width: 0; }
.candidate__heading, .candidate__actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-3); }
.candidate__heading { justify-content: space-between; }
h3 { color: var(--c-ink); font-size: var(--fs-lg); } h4 { font-size: var(--fs-md); color: var(--c-ink); }
.candidate__hint, .candidate__heading > span, .candidate__actions > span { font-size: var(--fs-sm); color: var(--c-text-secondary); line-height: 1.7; }
.candidate__quality { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-3); }
.candidate__quality > div { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--sp-2); padding: var(--sp-3); background: var(--c-bg); border: 1px solid var(--c-border); border-radius: var(--r-md); font-size: var(--fs-sm); }
.candidate__form { display: grid; gap: var(--sp-3); padding: var(--sp-5); background: var(--c-bg); border: 1px solid var(--c-border); border-radius: var(--r-md); }
fieldset { margin: 0; padding: 0; border: 0; display: grid; grid-template-columns: 1fr 2fr 1fr 1fr; gap: var(--sp-3); min-width: 0; }
label { display: grid; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--c-text-secondary); }
input:not([type=checkbox]), select, textarea { width: 100%; min-width: 0; border: 1px solid var(--c-border-strong); border-radius: var(--r-sm); padding: 9px 10px; color: var(--c-text); background: var(--c-surface); }
textarea { resize: vertical; } .candidate__wide { grid-column: 1 / -1; }
.candidate__confirmation { display: flex; align-items: flex-start; gap: var(--sp-2); line-height: 1.6; color: var(--c-text); }
.candidate__confirmation input { margin-top: 4px; flex-shrink: 0; }
.candidate__error { color: var(--c-danger); font-size: var(--fs-sm); line-height: 1.7; }
small { display: block; margin-top: 5px; color: var(--c-text-secondary); line-height: 1.6; }
td { min-width: 170px; max-width: 340px; overflow-wrap: anywhere; vertical-align: top !important; }
.candidate__literal { font-size: var(--fs-xs); margin-top: var(--sp-2); white-space: pre-wrap; }
.candidate__link { border: 0; padding: 6px 0 0; color: var(--c-primary); background: transparent; cursor: pointer; }
.candidate__missing { display: block; margin-top: 6px; color: var(--c-warning); }
details { min-width: 0; font-size: var(--fs-sm); } summary { cursor: pointer; color: var(--c-text-secondary); line-height: 1.6; }
pre { padding: var(--sp-3); background: var(--c-bg); border-radius: var(--r-sm); max-height: 300px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--fs-xs); }
.candidate__steps, .candidate__excluded ul { margin: 0; padding-left: 22px; font-size: var(--fs-sm); line-height: 1.8; overflow-wrap: anywhere; }
.candidate__excluded { display: grid; gap: var(--sp-2); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
@media (max-width: 900px) { .candidate__quality { grid-template-columns: repeat(2, minmax(0, 1fr)); } fieldset { grid-template-columns: 1fr 1fr; } }
@media (max-width: 640px) { .candidate__form { padding: var(--sp-3); } .candidate__quality { grid-template-columns: 1fr; } }
</style>
