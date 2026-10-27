<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import PageHeader from "@/components/layout/PageHeader.vue";
import PortalShell from "@/components/layout/PortalShell.vue";
import AppBadge from "@/components/ui/AppBadge.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCard from "@/components/ui/AppCard.vue";
import AppEmpty from "@/components/ui/AppEmpty.vue";
import AppTable from "@/components/ui/AppTable.vue";
import FinancialSubmissionDetail from "@/components/submission/FinancialSubmissionDetail.vue";
import {
  fetchAdminFinancialSubmissions, fetchFinancialSubmission, fetchFinancialSubmissions,
  publishFinancialSubmissions, retryFinancialSubmission, submitFinancialSubmission,
} from "@/services/api";
import type { FinancialPublication, FinancialSubmission } from "@/types/financialSubmissions";
import { reportPeriod } from "@/utils/financialDisplay";
import { processingLabels, reviewLabels, reviewVariant } from "@/utils/financialSubmission";

const props = withDefaults(defineProps<{ admin?: boolean }>(), { admin: false });
const route = useRoute();
const router = useRouter();
const page = computed(() => /^\d+$/.test(String(route.query.page || "")) && Number(route.query.page) > 0 ? Number(route.query.page) : 1);
const selectedId = computed(() => /^\d+$/.test(String(route.query.id || "")) && Number(route.query.id) > 0 ? Number(route.query.id) : null);
const filter = computed(() => props.admin ? String(route.query.reviewStatus || "WAITING") : "");
const records = ref<FinancialSubmission[]>([]);
const total = ref(0);
const loading = ref(true);
const listError = ref("");
const detailLoading = ref(false);
const detailError = ref("");
const selected = ref<FinancialSubmission | null>(null);
const selectedIds = ref<number[]>([]);
const file = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const uploadError = ref("");
const actionError = ref("");
const notice = ref("");
const uploading = ref(false);
const retrying = ref(false);
const reviewing = ref(false);
const publishing = ref(false);
const publication = ref<FinancialPublication | null>(null);
const busy = computed(() => uploading.value || retrying.value || reviewing.value || publishing.value);
let listGeneration = 0;
let detailGeneration = 0;
let timer: number | null = null;
let destroyed = false;
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 20)));
const filters = [{ value: "WAITING", label: "待审核" }, { value: "APPROVED", label: "已批准，待发布" }, { value: "PROCESSING", label: "处理中" }, { value: "REJECTED", label: "已拒绝" }, { value: "PUBLISHED", label: "已发布" }, { value: "ALL", label: "全部状态" }];

function message(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback; }
async function loadList() {
  const request = ++listGeneration;
  loading.value = true;
  try {
    const params = { page: page.value, size: 20 };
    const result = props.admin ? await fetchAdminFinancialSubmissions({ ...params, ...(filter.value === "ALL" ? {} : { reviewStatus: filter.value }) }) : await fetchFinancialSubmissions(params);
    if (request !== listGeneration || destroyed) return;
    records.value = result.records;
    total.value = result.total;
    listError.value = "";
    selectedIds.value = selectedIds.value.filter(id => result.records.some(item => item.id === id && item.reviewStatus === "APPROVED"));
  } catch (cause) {
    if (request === listGeneration && !destroyed) listError.value = message(cause, "提交记录加载失败，请重试。");
  } finally {
    if (request === listGeneration && !destroyed) loading.value = false;
  }
}
async function loadDetail() {
  const request = ++detailGeneration;
  const id = selectedId.value;
  if (!id) { detailLoading.value = false; return; }
  detailLoading.value = true;
  try {
    const result = await fetchFinancialSubmission(id);
    if (request !== detailGeneration || destroyed) return;
    selected.value = result;
    detailError.value = "";
  } catch (cause) {
    if (request === detailGeneration && !destroyed) detailError.value = message(cause, "提交详情加载失败，请重试。");
  } finally {
    if (request === detailGeneration && !destroyed) detailLoading.value = false;
  }
}
async function refresh() { await Promise.allSettled([loadList(), loadDetail()]); }
function select(id: number | null) {
  if (busy.value) return;
  const query = { ...route.query };
  if (id) query.id = String(id); else delete query.id;
  void router.push({ path: route.path, query });
}
function setFilter(value: string) {
  if (busy.value) return;
  void router.push({ path: route.path, query: { ...route.query, reviewStatus: value, page: "1" } });
}
function turnPage(delta: number) {
  if (busy.value) return;
  void router.push({ path: route.path, query: { ...route.query, page: String(page.value + delta) } });
}
function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const next = input.files?.[0];
  uploadError.value = "";
  notice.value = "";
  file.value = null;
  if (!next) return;
  if (!/\.pdf$/i.test(next.name) || (next.type && next.type !== "application/pdf")) {
    uploadError.value = "请选择 PDF 格式的财报文件。";
    input.value = "";
    return;
  }
  file.value = next;
}
async function upload() {
  if (!file.value || busy.value) return;
  uploading.value = true;
  actionError.value = "";
  notice.value = "";
  try {
    const result = await submitFinancialSubmission(file.value);
    if (destroyed) return;
    notice.value = `已接收 ${result.fileName}（提交 #${result.id}）：${processingLabels[result.status] || result.status}，${reviewLabels[result.reviewStatus] || result.reviewStatus}。`;
    file.value = null;
    if (fileInput.value) fileInput.value.value = "";
    await router.replace({ path: route.path, query: { ...route.query, page: "1", id: String(result.id) } });
    await refresh();
  } catch (cause) {
    if (!destroyed) actionError.value = message(cause, "提交失败，请重试。");
  } finally { uploading.value = false; }
}
async function retry() {
  if (!selected.value || busy.value) return;
  retrying.value = true;
  actionError.value = "";
  notice.value = "";
  try {
    const result = await retryFinancialSubmission(selected.value.id);
    if (destroyed) return;
    selected.value = result;
    notice.value = `服务端已接收重试：${processingLabels[result.status] || result.status}。`;
    await refresh();
  } catch (cause) { if (!destroyed) actionError.value = message(cause, "重试未受理，请稍后重试。"); }
  finally { retrying.value = false; }
}
function onReviewBusy(value: boolean) {
  reviewing.value = value;
  if (value) { listGeneration += 1; detailGeneration += 1; loading.value = false; detailLoading.value = false; }
  else if (!destroyed) void refresh();
}
function onReviewed(result: FinancialSubmission) {
  selected.value = result;
  notice.value = `审核已保存：${reviewLabels[result.reviewStatus] || result.reviewStatus}。`;
}
function toggleSelection(item: FinancialSubmission) {
  if (busy.value || item.reviewStatus !== "APPROVED") return;
  selectedIds.value = selectedIds.value.includes(item.id) ? selectedIds.value.filter(id => id !== item.id) : [...selectedIds.value, item.id];
}
async function publish() {
  if (busy.value || !props.admin) return;
  const ids = selectedIds.value.filter(id => records.value.some(item => item.id === id && item.reviewStatus === "APPROVED"));
  if (!ids.length) return;
  publishing.value = true;
  publication.value = null;
  actionError.value = "";
  notice.value = "";
  try {
    const result = await publishFinancialSubmissions(ids);
    if (destroyed) return;
    publication.value = result;
    selectedIds.value = [];
    await refresh();
  } catch (cause) { if (!destroyed) actionError.value = `发布未完成，已保留本次选择。${message(cause, "请稍后重试。")}`; }
  finally { publishing.value = false; }
}

watch([page, filter], () => { selectedIds.value = []; void loadList(); }, { immediate: true });
watch(selectedId, () => { selected.value = null; detailError.value = ""; void loadDetail(); }, { immediate: true });
function poll() {
  if (document.hidden || busy.value || loading.value || detailLoading.value) return;
  const active = props.admin || [...records.value, ...(selected.value ? [selected.value] : [])].some(item => ["PROCESSING", "WAITING", "APPROVED"].includes(item.reviewStatus));
  if (active) void refresh();
}
function onVisibility() { if (!document.hidden) poll(); }
onMounted(() => { timer = window.setInterval(poll, 8000); document.addEventListener("visibilitychange", onVisibility); });
onBeforeUnmount(() => { destroyed = true; listGeneration += 1; detailGeneration += 1; if (timer !== null) window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibility); });
</script>

<template>
  <PortalShell>
    <PageHeader :title="admin ? '财报审核与发布' : '我的财报提交'" :description="admin ? '逐份对照原始 PDF 核对身份与事实；批准后，选中需要发布的财报。' : '提交财报 PDF，持续查看处理、审核与发布状态。记录仅本人和管理员可见。'">
      <template #actions><router-link v-if="admin" to="/admin">返回管理端</router-link><AppButton variant="outline" :disabled="busy" :loading="loading" @click="refresh">刷新记录</AppButton></template>
    </PageHeader>
    <AppCard v-if="!admin" padding="lg" class="submissions__upload">
      <form @submit.prevent="upload"><div><h2>提交财报 PDF</h2><p>识别完成后由管理员审核。只有批准并发布的资料才用于公开查询和数字问答。</p></div><label>选择财报 PDF<input ref="fileInput" type="file" accept=".pdf,application/pdf" :disabled="busy" @change="chooseFile" /></label><span v-if="file" class="submissions__filename">{{ file.name }}</span><AppButton type="submit" :disabled="!file || busy" :loading="uploading">{{ uploading ? '正在上传…' : '提交 PDF' }}</AppButton></form>
      <p v-if="uploadError" class="submissions__error" role="alert">{{ uploadError }}</p>
    </AppCard>
    <p v-if="notice" role="status" class="submissions__notice">{{ notice }}</p>
    <p v-if="actionError" role="alert" class="submissions__error">{{ actionError }}</p>
    <AppCard v-if="admin" class="submissions__publication">
      <div class="submissions__toolbar"><div><h2>选定发布</h2><p>仅可选择本页已批准财报。每份资料须先完成审核；发布会保留当前版本中未被替换的报告。</p></div><AppButton :disabled="!selectedIds.length || busy || loading" :loading="publishing" @click="publish">{{ publishing ? '正在发布…' : `发布选中财报（${selectedIds.length}）` }}</AppButton></div>
      <p v-if="publication" role="status" class="submissions__notice">发布版本：{{ publication.version }} · 报告 {{ publication.reports }} 份 · 事实 {{ publication.facts }} 条</p>
      <p v-else class="submissions__muted">发布结果以服务端回执为准。</p>
    </AppCard>
    <section class="submissions__records" aria-label="财报提交记录">
      <div class="submissions__toolbar"><div><h2>{{ admin ? '审核队列' : '提交记录' }}</h2><p>共 {{ total }} 份 · 状态自动更新，刷新后可继续查看。</p></div><label v-if="admin" class="submissions__filter">审核状态<select :value="filter" :disabled="busy" aria-label="筛选审核状态" @change="setFilter(($event.target as HTMLSelectElement).value)"><option v-for="item in filters" :key="item.value" :value="item.value">{{ item.label }}</option></select></label></div>
      <p v-if="listError" role="alert" class="submissions__error">{{ listError }}<span v-if="records.length"> 正在显示最近一次成功加载的记录。</span><AppButton variant="outline" size="sm" :disabled="busy" @click="refresh">重新加载记录</AppButton></p>
      <p v-if="loading && !records.length" role="status" class="submissions__muted">正在加载提交记录…</p>
      <AppTable v-if="records.length" :loading="loading">
        <thead><tr><th v-if="admin">选择发布</th><th>财报文件 / 公司</th><th>报告期</th><th>处理状态</th><th>审核与发布</th><th>提交时间</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in records" :key="item.id" :class="{ 'submissions__selected': selectedId === item.id }">
          <td v-if="admin"><input type="checkbox" :aria-label="`选择发布 ${item.fileName}`" :checked="selectedIds.includes(item.id)" :disabled="item.reviewStatus !== 'APPROVED' || busy || loading" @change="toggleSelection(item)" /></td>
          <td><strong class="submissions__file">{{ item.fileName }}</strong><small>{{ item.company || '公司待识别' }}<template v-if="item.stockCode"> · {{ item.stockCode }}</template></small><small>提交 #{{ item.id }}</small></td>
          <td>{{ reportPeriod(item.reportYear, item.reportPeriod || '') }}</td><td>{{ processingLabels[item.status] || item.status }}</td>
          <td><AppBadge :variant="reviewVariant(item.reviewStatus)">{{ reviewLabels[item.reviewStatus] || '状态未登记' }}</AppBadge><small v-if="item.publishedVersion">版本 {{ item.publishedVersion }}</small></td>
          <td>{{ item.createdAt || '未登记' }}</td><td><AppButton variant="ghost" size="sm" :disabled="busy" @click="select(item.id)">{{ admin ? '核对详情' : '查看详情' }}</AppButton></td>
        </tr></tbody>
      </AppTable>
      <AppEmpty v-else-if="!loading && !listError" :title="admin ? '此状态暂无提交记录' : '还没有提交记录'" :description="admin ? '可切换审核状态查看其他候选。' : '选择一份财报 PDF 开始提交。'" />
      <nav v-if="total > 20 || page > 1" aria-label="提交记录分页" class="submissions__pagination"><AppButton variant="outline" size="sm" :disabled="page <= 1 || busy || loading" @click="turnPage(-1)">上一页</AppButton><span>第 {{ page }} / {{ pageCount }} 页</span><AppButton variant="outline" size="sm" :disabled="page >= pageCount || busy || loading" @click="turnPage(1)">下一页</AppButton></nav>
    </section>
    <p v-if="detailError" role="alert" class="submissions__error">{{ detailError }}<AppButton size="sm" variant="outline" :disabled="busy" @click="loadDetail">重新加载详情</AppButton></p>
    <p v-if="detailLoading && !selected" role="status" class="submissions__muted">正在加载提交详情…</p>
    <FinancialSubmissionDetail v-if="selected" :submission="selected" :admin="admin" :busy="busy" :retrying="retrying" @close="select(null)" @retry="retry" @changed="onReviewed" @busy="onReviewBusy" />
  </PortalShell>
</template>

<style scoped>
h2 { font-size: var(--fs-lg); color: var(--c-ink); } p { font-size: var(--fs-sm); line-height: 1.7; color: var(--c-text-secondary); }
.submissions__upload form { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: var(--sp-4); }
.submissions__upload form > div { grid-column: 1 / -1; } .submissions__upload label { display: grid; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--c-text-secondary); }
.submissions__upload input { display: block; max-width: 100%; font-size: var(--fs-sm); padding: var(--sp-3); background: var(--c-bg); border: 1px solid var(--c-border); border-radius: var(--r-md); }
.submissions__filename { display: none; } .submissions__toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: var(--sp-4); }
.submissions__toolbar > div { flex: 1 1 320px; } .submissions__records { display: grid; gap: var(--sp-4); }
.submissions__filter { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--c-text-secondary); }
select { padding: 8px 10px; border: 1px solid var(--c-border-strong); border-radius: var(--r-sm); background: var(--c-surface); color: var(--c-text); }
.submissions__error, .submissions__notice { padding: var(--sp-3) var(--sp-4); border: 1px solid var(--c-border); border-radius: var(--r-md); overflow-wrap: anywhere; display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-3); }
.submissions__error { color: var(--c-danger); background: var(--c-danger-soft); } .submissions__notice { color: var(--c-primary); background: var(--c-primary-soft); }
.submissions__publication .submissions__notice, .submissions__publication .submissions__muted { margin-top: var(--sp-3); }
.submissions__muted { color: var(--c-text-secondary); } small { display: block; margin-top: 5px; font-size: var(--fs-xs); line-height: 1.6; }
.submissions__file { color: var(--c-text); display: block; max-width: 260px; overflow-wrap: anywhere; } .submissions__selected { background: var(--c-primary-soft); }
.submissions__pagination { display: flex; justify-content: end; align-items: center; gap: var(--sp-3); font-size: var(--fs-sm); }
@media (max-width: 640px) { .submissions__upload form { grid-template-columns: minmax(0, 1fr); } .submissions__upload form > div { grid-column: auto; } .submissions__upload form > button { justify-self: start; } .submissions__pagination { justify-content: center; } }
</style>
