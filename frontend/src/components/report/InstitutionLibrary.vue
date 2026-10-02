<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";


import ReportDetailModal from "@/components/report/ReportDetailModal.vue";
import { IconGrid, IconSearch, IconTable } from "@/components/ui/icons";
import { fetchReports, fetchReportDetail } from "@/services/api";
import type { ResearchReportItem } from "@/types/platform";

type ViewMode = "card" | "table";

const router = useRouter();

const records = ref<ResearchReportItem[]>([]);
const total = ref(0);
const page = ref(1);
const size = ref(12);
const loading = ref(false);
const error = ref("");
let requestVersion = 0;
const keyword = ref("");
const reportType = ref("");
const viewMode = ref<ViewMode>("card");

const selectedReport = ref<ResearchReportItem | null>(null);
const detailLoading = ref(false);

const typeOptions = [
  { value: "", label: "全部" },
  { value: "stock", label: "个股研报" },
  { value: "industry", label: "行业研报" },
];

async function load() {
  const version = ++requestVersion;
  loading.value = true; error.value = "";
  try {
    const result = await fetchReports({
      page: page.value,
      size: size.value,
      keyword: keyword.value || undefined,
      reportType: reportType.value || undefined,
    });
    if(version !== requestVersion) return;
    records.value = result.records;
    total.value = result.total;
  } catch (e) {
    if(version === requestVersion) error.value = e instanceof Error ? e.message : "资料加载失败";
  } finally {
    if(version === requestVersion) loading.value = false;
  }
}

onMounted(load);
watch([page, reportType, size], load);

function search() {
  if (page.value === 1) {
    void load();
    return;
  }
  page.value = 1;
}

function setView(mode: ViewMode) {
  if (viewMode.value === mode) return;
  viewMode.value = mode;
  size.value = mode === "table" ? 20 : 12;
  page.value = 1;
}

function setReportType(value: string) {
  if (reportType.value === value) return;
  reportType.value = value;
  page.value = 1;
}

const maxPage = () => Math.max(1, Math.ceil(total.value / size.value));

function openDetail(report: ResearchReportItem) {
  detailLoading.value = true;
  fetchReportDetail(report.id)
    .then((detail) => {
      selectedReport.value = detail;
    })
    .catch(() => {
      selectedReport.value = report;
    })
    .finally(() => {
      detailLoading.value = false;
    });
}

function closeDetail() {
  selectedReport.value = null;
}

function goToCompany(report: ResearchReportItem, event?: MouseEvent) {
  event?.stopPropagation();
  if (report.stockCode) {
    router.push(`/market/${report.stockCode}`);
    closeDetail();
  }
}

function formatEntity(report: ResearchReportItem) {
  if (report.reportType === "stock" && report.stockName) {
    return `${report.stockName} ${report.stockCode || ""}`;
  }
  return report.industryName || "—";
}
</script>

<template>
  <div class="reports">
    <div class="reports__main">
      <div class="reports__toolbar">
        <p class="reports__sub">
          共 <strong>{{ total }}</strong> 篇研究报告 · 点击卡片阅读正文
        </p>
        <div class="reports__controls">
          <div class="reports__search-wrap">
            <IconSearch :size="14" />
            <input
              v-model="keyword"
              class="reports__search"
              type="search"
              placeholder="搜索标题 / 机构 / 公司，回车确认"
              aria-label="搜索机构研报"
              @keydown.enter="search"
            />
          </div>
          <div class="segmented" role="group" aria-label="研报类型">
            <button
              v-for="opt in typeOptions"
              :key="opt.value"
              type="button"
              class="segmented__btn"
              :class="{ 'segmented__btn--active': reportType === opt.value }"
              @click="setReportType(opt.value)"
            >
              {{ opt.label }}
            </button>
          </div>
          <div class="segmented" role="group" aria-label="显示方式">
            <button
              type="button"
              class="segmented__btn segmented__btn--icon"
              :class="{ 'segmented__btn--active': viewMode === 'card' }"
              title="卡片"
              aria-label="卡片视图"
              @click="setView('card')"
            >
              <IconGrid :size="14" />
            </button>
            <button
              type="button"
              class="segmented__btn segmented__btn--icon"
              :class="{ 'segmented__btn--active': viewMode === 'table' }"
              title="表格"
              aria-label="表格视图"
              @click="setView('table')"
            >
              <IconTable :size="14" />
            </button>
          </div>
        </div>
      </div>

      <div v-if="loading && !records.length" class="reports__empty">加载中…</div>
      <div v-else-if="error" class="reports__empty" role="alert">{{ error }} <button @click="load">重试</button></div>
      <div v-else-if="!records.length" class="reports__empty">{{ keyword || reportType ? '没有匹配的机构研报，请调整筛选条件。' : '当前环境尚未收录机构研报。已导入的公司年报、半年报请在“公司财报”中查看。' }}</div>

      <template v-else>
        <!-- 作品说明：卡片视图 -->
        <div v-if="viewMode === 'card'" class="reports__grid">
          <article
            v-for="report in records"
            :key="report.id"
            class="report-card"
            :class="{ 'report-card--loading': detailLoading }"
            tabindex="0"
            role="button"
            :aria-label="`阅读研报：${report.title}`"
            @click="openDetail(report)"
            @keydown.enter.self="openDetail(report)"
            @keydown.space.self.prevent="openDetail(report)"
          >
            <div class="report-card__header">
              <span
                class="report-card__type"
                :class="{ 'report-card__type--industry': report.reportType === 'industry' }"
              >
                {{ report.reportType === "stock" ? "个股" : "行业" }}
              </span>
              <span
                v-if="report.rating"
                class="rating-badge"
                :class="{ 'rating-badge--buy': report.rating.includes('买') }"
              >
                {{ report.rating }}
              </span>
              <span class="report-card__date mono">{{ report.publishDate ?? "" }}</span>
            </div>

            <h3 class="report-card__title">{{ report.title }}</h3>

            <div class="report-card__entity">
              {{ formatEntity(report) }}
            </div>

            <div class="report-card__org">
              {{ report.orgSname || report.orgName || "—" }}
              <span v-if="report.researcher">· {{ report.researcher }}</span>
            </div>

            <div class="report-card__foot">
              <div class="metric-pills">
                <span v-if="report.predictThisYearEps" class="pill">EPS {{ report.predictThisYearEps }}</span>
                <span v-if="report.predictThisYearPe" class="pill">PE {{ report.predictThisYearPe }}</span>
                <span v-if="report.aimPrice" class="pill">目标价 {{ report.aimPrice }}</span>
                <span v-if="report.pdfPresent" class="pill pill--pdf">原文已收录</span>
              </div>
              <button
                v-if="report.stockCode"
                type="button"
                class="report-card__link"
                @click="goToCompany(report, $event)"
              >
                公司详情 →
              </button>
            </div>
          </article>
        </div>

        <!-- 作品说明：表格视图 -->
        <div v-else class="table-wrap">
          <table class="report-table">
            <thead>
              <tr>
                <th class="col-type">类型</th>
                <th class="col-date">日期</th>
                <th class="col-title">标题</th>
                <th class="col-entity">公司/行业</th>
                <th class="col-org">机构</th>
                <th class="col-rating">评级</th>
                <th class="col-metrics">预测</th>
                <th class="col-action">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="report in records"
                :key="report.id"
                class="report-table__row"
                :class="{ 'report-table__row--loading': detailLoading }"
                @click="openDetail(report)"
              >
                <td>
                  <span
                    class="type-tag"
                    :class="{ 'type-tag--industry': report.reportType === 'industry' }"
                  >
                    {{ report.reportType === "stock" ? "个股" : "行业" }}
                  </span>
                </td>
                <td class="mono">{{ report.publishDate ?? "—" }}</td>
                <td class="report-table__title">{{ report.title }}</td>
                <td>{{ formatEntity(report) }}</td>
                <td>{{ report.orgSname || report.orgName || "—" }}</td>
                <td>
                  <span
                    v-if="report.rating"
                    class="rating-badge"
                    :class="{ 'rating-badge--buy': report.rating.includes('买') }"
                  >
                    {{ report.rating }}
                  </span>
                  <span v-else>—</span>
                </td>
                <td>
                  <div class="metric-pills metric-pills--compact">
                    <span v-if="report.predictThisYearEps" class="pill">EPS {{ report.predictThisYearEps }}</span>
                    <span v-if="report.predictThisYearPe" class="pill">PE {{ report.predictThisYearPe }}</span>
                  </div>
                </td>
                <td>
                  <button
                    v-if="report.stockCode"
                    type="button"
                    class="table-link"
                    @click="goToCompany(report, $event)"
                  >
                    详情
                  </button>
                  <span v-else class="table-placeholder">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <ReportDetailModal :report="selectedReport" @close="closeDetail" />

      <div v-if="total > size" class="reports__pager">
        <button type="button" :disabled="page <= 1" @click="page -= 1">上一页</button>
        <span class="mono">{{ page }} / {{ maxPage() }}</span>
        <button type="button" :disabled="page >= maxPage()" @click="page += 1">下一页</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reports__main {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.reports__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--sp-3);
}

.reports__sub {
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.reports__sub strong {
  color: var(--c-ink);
  font-weight: 600;
}

.reports__controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.reports__search-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.reports__search-wrap > svg {
  position: absolute;
  left: 12px;
  color: var(--c-text-tertiary);
  pointer-events: none;
}

.reports__search {
  width: 280px;
  height: 36px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 0 var(--sp-4) 0 34px;
  font-size: var(--fs-sm);
  background: var(--c-surface);
  color: var(--c-text);
}

.reports__search:focus {
  outline: none;
  border-color: var(--c-primary-border);
}

.segmented {
  display: inline-flex;
  padding: 3px;
  border-radius: var(--r-full);
  background: var(--c-bg);
  border: 1px solid var(--c-border);
}

.segmented__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: var(--r-full);
  padding: 5px var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: background var(--t-fast), color var(--t-fast);
}

.segmented__btn--icon {
  padding: 6px 9px;
}

.segmented__btn--active {
  background: var(--c-surface);
  color: var(--c-ink);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.reports__empty {
  text-align: center;
  padding: var(--sp-12);
  color: var(--c-text-tertiary);
  background: var(--c-surface);
  border: 1px dashed var(--c-border);
  border-radius: var(--r-lg);
}

/* 作品说明：卡片视图 */
.reports__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--sp-4);
}

.report-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  padding: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  cursor: pointer;
  transition: border-color var(--t-fast), box-shadow var(--t-fast), transform var(--t-fast);
}

.report-card:hover {
  border-color: var(--c-primary-border);
  box-shadow: var(--shadow-md, 0 4px 16px rgb(0 0 0 / 8%));
  transform: translateY(-1px);
}

.report-card--loading {
  opacity: 0.7;
  pointer-events: none;
}

.report-card__header {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.report-card__type {
  font-size: var(--fs-xs);
  font-weight: 600;
  border-radius: var(--r-full);
  padding: 2px 8px;
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.report-card__type--industry {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.report-card__date {
  margin-left: auto;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.report-card__rating {
  display: flex;
}

.rating-badge {
  display: inline-flex;
  align-items: center;
  font-size: var(--fs-xs);
  font-weight: 600;
  border-radius: var(--r-full);
  padding: 2px 9px;
  background: var(--c-bg);
  color: var(--c-text-secondary);
}

.rating-badge--buy {
  background: var(--c-danger-soft);
  color: var(--c-danger);
}

.report-card__title {
  font-size: var(--fs-md);
  font-weight: 700;
  line-height: 1.5;
  color: var(--c-ink);
  margin-top: var(--sp-1);
}

.report-card__entity {
  font-size: var(--fs-sm);
  color: var(--c-text);
  font-weight: 500;
}

.report-card__org {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.report-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-top: auto;
  padding-top: var(--sp-2);
  border-top: 1px solid var(--c-border);
}

.metric-pills {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.metric-pills--compact {
  gap: 4px;
}

.pill {
  font-size: var(--fs-xs);
  padding: 2px 8px;
  border-radius: var(--r-full);
  background: var(--c-bg);
  color: var(--c-text-secondary);
  white-space: nowrap;
}

.pill--pdf {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.report-card__link {
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: var(--c-primary);
  font-size: var(--fs-xs);
  font-weight: 500;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--r-full);
  transition: background var(--t-fast);
}

.report-card__link:hover {
  background: var(--c-primary-soft);
  text-decoration: none;
}

/* 作品说明：表格视图 */
.table-wrap {
  width: 100%;
  overflow-x: auto;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.report-table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.report-table thead {
  background: var(--c-bg);
  border-bottom: 1px solid var(--c-border);
}

.report-table th {
  text-align: left;
  padding: 12px var(--sp-4);
  font-weight: 600;
  color: var(--c-text-secondary);
  white-space: nowrap;
}

.report-table td {
  padding: 14px var(--sp-4);
  border-bottom: 1px solid var(--c-border);
  color: var(--c-text-secondary);
  vertical-align: middle;
}

.report-table tbody tr:last-child td {
  border-bottom: none;
}

.report-table__row {
  cursor: pointer;
  transition: background var(--t-fast);
}

.report-table__row:hover {
  background: var(--c-bg);
}

.report-table__row--loading {
  opacity: 0.7;
  pointer-events: none;
}

.report-table__title {
  color: var(--c-text);
  font-weight: 500;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.type-tag {
  display: inline-flex;
  white-space: nowrap;
  font-size: var(--fs-xs);
  font-weight: 600;
  border-radius: var(--r-full);
  padding: 2px 8px;
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.report-table td:nth-child(2) { white-space: nowrap; }

.type-tag--industry {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.table-link {
  border: none;
  background: transparent;
  color: var(--c-primary);
  font-size: var(--fs-xs);
  font-weight: 500;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--r-full);
  transition: background var(--t-fast);
}

.table-link:hover {
  background: var(--c-primary-soft);
}

.table-placeholder {
  color: var(--c-text-tertiary);
}

.col-type { width: 70px; }
.col-date { width: 100px; }
.col-title { width: auto; }
.col-entity { width: 160px; }
.col-org { width: 130px; }
.col-rating { width: 90px; }
.col-metrics { width: 140px; }
.col-action { width: 70px; }

/* 作品说明：分页 */
.reports__pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-4);
  margin-top: var(--sp-2);
}

.reports__pager button {
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  border-radius: var(--r-full);
  padding: 7px var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast);
}

.reports__pager button:hover:not(:disabled) {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

.reports__pager button:disabled {
  opacity: 0.45;
  cursor: default;
}

.mono {
  font-family: var(--font-mono);
}

@media (max-width: 900px) {
  .reports__controls,
  .reports__search-wrap,
  .reports__search {
    width: 100%;
  }

  .segmented:first-of-type {
    flex: 1;
  }

  .segmented:first-of-type .segmented__btn {
    flex: 1;
  }

  .reports__grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .report-table { min-width: 0; table-layout: fixed; }
  .report-table th:not(.col-date):not(.col-title),
  .report-table td:not(:nth-child(2)):not(:nth-child(3)) { display: none; }
  .report-table .col-date { width: 100px; }
  .report-table th, .report-table td { padding: 12px; }
  .report-table__title { white-space: normal; overflow-wrap: anywhere; }
}
</style>
