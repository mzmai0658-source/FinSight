<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { EChartsOption } from "echarts";

import AppBadge from "@/components/ui/AppBadge.vue";
import AppCard from "@/components/ui/AppCard.vue";
import AppEmpty from "@/components/ui/AppEmpty.vue";
import AppSection from "@/components/ui/AppSection.vue";
import AppSkeleton from "@/components/ui/AppSkeleton.vue";
import EChart from "@/components/market/EChart.vue";
import PageHeader from "@/components/layout/PageHeader.vue";
import PortalShell from "@/components/layout/PortalShell.vue";
import { IconAlert, IconChevronRight, IconSearch } from "@/components/ui/icons";
import {
  AXIS_STYLE,
  GRID_STYLE,
  LEGEND_STYLE,
  PALETTE,
  TOOLTIP_STYLE,
  axisCount,
  axisPct,
  axisYi,
  fmtChartPct,
  fmtChartYi,
  fmtCount,
  toNum,
  toYi,
} from "@/components/market/chartTheme";
import { fetchCompanies, fetchMarketSummary } from "@/services/api";
import type { CompanyItem, MarketSummary } from "@/types/market";

type Segment = "imported" | "pending" | "all";

const PAGE_SIZE = 15;

const router = useRouter();
const route = useRoute();

const summary = ref<MarketSummary | null>(null);
const companies = ref<CompanyItem[]>([]);
const loading = ref(true);
const error = ref("");

function setFilter(key: string, value: string) {
  void router.replace({ query: { ...route.query, [key]: value || undefined, page: undefined } });
}
const keyword = computed({
  get: () => typeof route.query.q === "string" ? route.query.q : "",
  set: (value: string) => setFilter("q", value),
});
const industryFilter = computed({
  get: () => typeof route.query.industry === "string" ? route.query.industry : "",
  set: (value: string) => setFilter("industry", value),
});
const segment = computed<Segment>({
  get: () => route.query.status === "all" || route.query.status === "pending" ? route.query.status : "imported",
  set: (value) => setFilter("status", value),
});
const page = computed(() => Math.min(pageCount.value, Math.max(1, Math.trunc(Number(route.query.page)) || 1)));
async function turnPage(delta: number) {
  const nextPage = page.value + delta;
  await router.replace({ query: { ...route.query, page: nextPage > 1 ? String(nextPage) : undefined } });
  await nextTick();
  document.getElementById("company-list")?.scrollIntoView({ behavior: "smooth", block: "start" });
}
function clearFilters() {
  void router.replace({ query: { status: "all" } });
}

onMounted(async () => {
  try {
    [summary.value, companies.value] = await Promise.all([fetchMarketSummary(), fetchCompanies()]);
    if (!summary.value.importedCompanies && !route.query.status) segment.value = "all";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败，请稍后重试";
  } finally {
    loading.value = false;
  }
});

const shortIndustry = (industry: string) => industry.replace(/^.*-/, "");

/** 作品说明：只有存在两个以上真实行业时，行业分布与行业筛选才有信息量 */
const industries = computed(() =>
  (summary.value?.industryDistribution ?? []).map((row) => row.industry).filter(Boolean),
);
const showIndustryChart = computed(() => industries.value.length >= 2);

/** 作品说明：主数据未登记的列整体隐藏，避免整列空白 */
const columns = computed(() => ({
  industry: companies.value.some((c) => c.industry),
  exchange: companies.value.some((c) => c.exchange),
  board: companies.value.some((c) => c.board),
}));

const importedCount = computed(() => summary.value?.importedCompanies ?? 0);
const pendingCount = computed(() => summary.value?.pendingCompanies ?? 0);
const companyTotal = computed(() => importedCount.value + pendingCount.value);
const importRate = computed(() =>
  companyTotal.value ? Math.round((importedCount.value / companyTotal.value) * 100) : 0,
);

const yearRows = computed(() => summary.value?.yearlyAggregate ?? []);
const yearSpan = computed(() => {
  const years = yearRows.value.map((r) => Number(r.year)).filter(Number.isFinite);
  if (!years.length) return "—";
  const min = Math.min(...years);
  const max = Math.max(...years);
  return min === max ? String(min) : `${min}–${max}`;
});

/** 作品说明：各年参与合计的公司数不同，跨年比较需要提示 */
const unevenYearCounts = computed(() => {
  const counts = new Set(yearRows.value.map((r) => Number(r.company_count)));
  return counts.size > 1;
});

const yearCountNote = computed(() =>
  yearRows.value.map((r) => `${r.year} 年 ${r.company_count ?? "—"} 家`).join(" · "),
);

const segments = computed(() => [
  { value: "imported" as const, label: "已入库", count: importedCount.value },
  { value: "pending" as const, label: "待入库", count: pendingCount.value },
  { value: "all" as const, label: "全部", count: companyTotal.value },
]);

const filteredCompanies = computed(() => {
  const kw = keyword.value;
  return companies.value
    .filter(
      (c) =>
        (segment.value === "all" || c.dataStatus === segment.value) &&
        (!kw || c.abbr.includes(kw) || c.stockCode.includes(kw) || c.fullName.includes(kw)) &&
        (!industryFilter.value || c.industry === industryFilter.value),
    )
    .sort((a, b) => {
      if (a.dataStatus !== b.dataStatus) return a.dataStatus === "imported" ? -1 : 1;
      return a.stockCode.localeCompare(b.stockCode);
    });
});

const pageCount = computed(() => Math.max(1, Math.ceil(filteredCompanies.value.length / PAGE_SIZE)));
const pagedCompanies = computed(() =>
  filteredCompanies.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
);


/* 作品说明：图表 */

const industryOption = computed<EChartsOption>(() => {
  const rows = [...(summary.value?.industryDistribution ?? [])].filter((r) => r.industry).reverse();
  return {
    color: PALETTE,
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value: unknown) => fmtCount(value),
    },
    legend: { ...LEGEND_STYLE, top: 0 },
    grid: { ...GRID_STYLE, top: 40 },
    xAxis: {
      type: "value",
      ...AXIS_STYLE,
      minInterval: 1,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisCount },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => shortIndustry(r.industry)),
      ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, width: 118, overflow: "truncate" },
    },
    series: [
      {
        name: "收录公司",
        type: "bar",
        data: rows.map((r) => r.total),
        barWidth: 12,
        itemStyle: { borderRadius: [0, 4, 4, 0], color: "#c4d4f2" },
      },
      {
        name: "已入库",
        type: "bar",
        barGap: "-100%",
        data: rows.map((r) => r.imported),
        barWidth: 12,
        itemStyle: { borderRadius: [0, 4, 4, 0], color: PALETTE[0] },
      },
    ],
  };
});

const yearlyOption = computed<EChartsOption>(() => {
  const rows = yearRows.value;
  return {
    color: PALETTE,
    tooltip: TOOLTIP_STYLE,
    legend: { ...LEGEND_STYLE, top: 0 },
    grid: { ...GRID_STYLE, top: 50 },
    xAxis: {
      type: "category",
      data: rows.map((r) => `${r.year}\n${r.company_count ?? "—"} 家`),
      ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, lineHeight: 16 },
    },
    yAxis: [
      {
        type: "value",
        name: "金额",
        ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisYi },
      },
      {
        type: "value",
        name: "ROE",
        ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisPct },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "营收合计",
        type: "bar",
        data: rows.map((r) => toYi(r.revenue_sum)),
        barWidth: 22,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
      {
        name: "净利润合计",
        type: "bar",
        data: rows.map((r) => toYi(r.net_profit_sum)),
        barWidth: 22,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
      {
        name: "平均 ROE",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        data: rows.map((r) => toNum(r.roe_avg)),
        symbolSize: 7,
        tooltip: { valueFormatter: fmtChartPct },
      },
    ],
  };
});

const rankOption = computed<EChartsOption>(() => {
  const rows = [...(summary.value?.companyRank ?? [])].slice(0, 10).reverse();
  return {
    color: PALETTE,
    tooltip: {
      ...TOOLTIP_STYLE,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: fmtChartYi,
    },
    grid: { ...GRID_STYLE, top: 12 },
    xAxis: {
      type: "value",
      splitNumber: 4,
      ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisYi },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => String(r.abbr)),
      ...AXIS_STYLE,
    },
    series: [
      {
        name: "营业收入",
        type: "bar",
        data: rows.map((r) => toYi(r.revenue)),
        barWidth: 14,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
    ],
  };
});

function onRankClick(params: { name?: string }) {
  const target = summary.value?.companyRank.find((r) => r.abbr === params.name);
  if (target) openCompany(String(target.code));
}

function openCompany(code: string) {
  router.push(`/market/${code}`);
}
</script>

<template>
  <PortalShell>
    <PageHeader
      title="公司数据"
      description="查看已结构化入库公司的年报指标，从营收排名或公司列表进入详情，再带着问题到 AI 工作台核对原始证据。"
    >
    </PageHeader>

    <div v-if="error" class="market__error" role="alert">{{ error }}</div>

    <AppCard v-else-if="loading" padding="lg">
      <AppSkeleton :lines="5" />
    </AppCard>

    <template v-else-if="summary">
      <!-- 作品说明：概览 -->
      <section class="kpis" aria-label="数据概览">
        <div class="kpi">
          <span class="kpi__label">已结构化入库</span>
          <strong class="kpi__value">{{ importedCount }}<small>/ {{ companyTotal }} 家</small></strong>
          <span class="kpi__bar" role="progressbar" :aria-valuenow="importRate" aria-valuemin="0" aria-valuemax="100">
            <span :style="{ width: `${importRate}%` }" />
          </span>
        </div>
        <div class="kpi">
          <span class="kpi__label">已收录待处理</span>
          <strong class="kpi__value">{{ pendingCount }}<small>家</small></strong>
          <span class="kpi__hint">资料可查阅，数字问答暂不覆盖</span>
        </div>
        <div class="kpi">
          <span class="kpi__label">年报年度</span>
          <strong class="kpi__value">{{ yearSpan }}</strong>
        </div>
        <div class="kpi">
          <span class="kpi__label">最新年报</span>
          <strong class="kpi__value">{{ summary.latestYear ?? "—" }}<small v-if="summary.latestYear">年</small></strong>
        </div>
      </section>

      <!-- 作品说明：图表 -->
      <div class="market__charts">
        <AppCard>
          <template #header>
            <AppSection
              title="营收排行"
              :description="`${summary.latestYear ?? ''} 年报 · 单位：亿元 · 点击柱条查看公司详情`"
              dense
            />
          </template>
          <EChart :option="rankOption" height="340px" @click="onRankClick" />
        </AppCard>
        <AppCard>
          <template #header>
            <AppSection title="已入库公司年度合计" description="柱：亿元 · 线：平均 ROE（%）· 横轴下方为参与合计的公司数" dense />
          </template>
          <EChart :option="yearlyOption" height="300px" />
          <p v-if="unevenYearCounts" class="market__note">
            <IconAlert :size="13" />
            各年参与合计的公司数不同（{{ yearCountNote }}），合计值不宜直接跨年比较。
          </p>
        </AppCard>
      </div>

      <div v-if="showIndustryChart" class="market__charts market__charts--secondary">
        <AppCard>
          <template #header>
            <AppSection title="行业分布" description="单位：家" dense />
          </template>
          <EChart :option="industryOption" height="300px" />
        </AppCard>
        <AppCard v-if="summary.hotCompanies?.length">
          <template #header>
            <AppSection title="近期关注" description="按公司详情浏览次数" dense />
          </template>
          <ol class="hot-list">
            <li
              v-for="(item, idx) in summary.hotCompanies"
              :key="item.stockCode"
              class="hot-list__item"
              @click="openCompany(item.stockCode)"
            >
              <span class="hot-list__rank" :class="{ 'hot-list__rank--top': idx < 3 }">{{ idx + 1 }}</span>
              <span class="hot-list__name">{{ item.abbr }}</span>
              <span class="hot-list__industry">{{ shortIndustry(item.industry) }}</span>
              <span class="hot-list__views">{{ fmtCount(item.views, "次") }}</span>
            </li>
          </ol>
        </AppCard>
      </div>

      <section v-else-if="summary.hotCompanies?.length" class="hot-strip" aria-label="近期关注">
        <span class="hot-strip__title">近期关注</span>
        <button
          v-for="(item, idx) in summary.hotCompanies.slice(0, 8)"
          :key="item.stockCode"
          type="button"
          class="hot-strip__item"
          @click="openCompany(item.stockCode)"
        >
          <span class="hot-strip__rank">{{ idx + 1 }}</span>
          {{ item.abbr }}
          <small>{{ fmtCount(item.views, "次") }}</small>
        </button>
      </section>

      <!-- 作品说明：公司列表 -->
      <AppCard id="company-list" padding="none" class="list">
        <div class="list__head">
          <div class="list__titles">
            <h2>公司列表</h2>
            <p>共 {{ filteredCompanies.length }} 家，点击行进入详情</p>
          </div>
          <div class="list__tools">
            <div class="segmented" role="tablist" aria-label="数据状态">
              <button
                v-for="item in segments"
                :key="item.value"
                type="button"
                role="tab"
                class="segmented__btn"
                :class="{ 'segmented__btn--active': segment === item.value }"
                :aria-selected="segment === item.value"
                @click="segment = item.value"
              >
                {{ item.label }}<span>{{ item.count }}</span>
              </button>
            </div>
            <label class="search">
              <IconSearch :size="14" />
              <input v-model.trim="keyword" type="search" placeholder="代码 / 简称" aria-label="搜索公司" />
            </label>
            <select v-if="industries.length >= 2" v-model="industryFilter" class="select" aria-label="行业">
              <option value="">全部行业</option>
              <option v-for="industry in industries" :key="industry" :value="industry">
                {{ shortIndustry(industry) }}
              </option>
            </select>
          </div>
        </div>

        <div class="list__table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th class="table__code">代码</th>
                <th>公司</th>
                <th v-if="columns.industry" class="table__extra">行业</th>
                <th v-if="columns.exchange" class="table__extra">交易所</th>
                <th v-if="columns.board" class="table__extra">板块</th>
                <th>数据状态</th>
                <th>报告期覆盖</th>
                <th class="table__go" aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="company in pagedCompanies"
                :key="company.stockCode"
                tabindex="0"
                @click="openCompany(company.stockCode)"
                @keydown.enter="openCompany(company.stockCode)"
              >
                <td class="mono table__code">{{ company.stockCode }}</td>
                <td class="table__abbr">{{ company.abbr }}<span class="table__mobile-code mono">{{ company.stockCode }}</span></td>
                <td v-if="columns.industry" class="table__extra">{{ shortIndustry(company.industry) || "—" }}</td>
                <td v-if="columns.exchange" class="table__extra">{{ company.exchange || "—" }}</td>
                <td v-if="columns.board" class="table__extra">{{ company.board.replace(/A股$/, "") || "—" }}</td>
                <td>
                  <AppBadge :variant="company.dataStatus === 'imported' ? 'success' : 'default'">
                    {{ company.dataStatus === "imported" ? "已入库" : "待入库" }}
                  </AppBadge>
                </td>
                <td class="mono table__muted">
                  {{ company.firstYear ? `${company.firstYear}–${company.lastYear}` : "—" }}
                </td>
                <td class="table__go"><IconChevronRight :size="14" /></td>
              </tr>
            </tbody>
          </table>
          <AppEmpty v-if="!filteredCompanies.length" title="没有匹配的公司" description="请尝试调整筛选条件">
            <button type="button" class="reset-filters" @click="clearFilters">清除筛选，查看全部公司</button>
          </AppEmpty>
        </div>

        <nav v-if="pageCount > 1" class="list__pager" aria-label="公司列表分页">
          <button type="button" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
          <span class="mono">{{ page }} / {{ pageCount }}</span>
          <button type="button" :disabled="page >= pageCount" @click="turnPage(1)">下一页</button>
        </nav>
      </AppCard>
    </template>
  </PortalShell>
</template>

<style scoped>
.reset-filters { border: 1px solid var(--c-primary-border); border-radius: var(--r-full); color: var(--c-primary); background: var(--c-surface); padding: 8px 16px; cursor: pointer; }
.market__error {
  padding: var(--sp-6);
  text-align: center;
  color: var(--c-danger);
  background: var(--c-danger-soft);
  border-radius: var(--r-lg);
}

/* 作品说明：概览 */
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.kpi {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--sp-4) var(--sp-5);
}

.kpi + .kpi {
  border-left: 1px solid var(--c-border);
}

.kpi__label {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.kpi__value {
  font-size: var(--fs-2xl);
  font-weight: 800;
  letter-spacing: -0.5px;
  line-height: 1.15;
  color: var(--c-ink);
  font-variant-numeric: tabular-nums;
}

.kpi__value small {
  margin-left: 4px;
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-text-tertiary);
}

.kpi__bar {
  display: block;
  height: 4px;
  margin-top: var(--sp-2);
  border-radius: var(--r-full);
  background: var(--c-primary-soft);
  overflow: hidden;
}

.kpi__bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--c-primary);
}

.kpi__hint {
  margin-top: var(--sp-1);
  font-size: 11px;
  color: var(--c-text-tertiary);
}

/* 作品说明：近期关注（无行业图时的紧凑形式） */
.hot-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-5);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.hot-strip__title {
  margin-right: var(--sp-2);
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--c-ink);
}

.hot-strip__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px var(--sp-3) 4px 4px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface);
  font-size: var(--fs-sm);
  color: var(--c-ink);
  cursor: pointer;
  transition: border-color var(--t-fast), color var(--t-fast);
}

.hot-strip__item:hover {
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

.hot-strip__rank {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-size: 11px;
  font-weight: 700;
}

.hot-strip__item small {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

/* 作品说明：图表 */
.market__charts {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
  gap: var(--sp-4);
}

.market__charts--secondary {
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
}

.market__note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-warning-soft);
  color: var(--c-warning);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.market__note svg {
  flex-shrink: 0;
  margin-top: 3px;
}

/* 作品说明：近期关注 */
.hot-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.hot-list__item {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: background var(--t-fast);
}

.hot-list__item:hover {
  background: var(--c-primary-soft);
}

.hot-list__rank {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--c-bg);
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
  font-weight: 700;
}

.hot-list__rank--top {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.hot-list__name {
  min-width: 68px;
  font-weight: 600;
  font-size: var(--fs-sm);
}

.hot-list__industry {
  flex: 1;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.hot-list__views {
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
}

/* 作品说明：公司列表 */
.list {
  overflow: hidden;
  scroll-margin-top: calc(var(--portal-nav-h) + var(--sp-4));
}

.list__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--sp-3) var(--sp-4);
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--c-border);
}

.list__titles h2 {
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-ink);
}

.list__titles p {
  margin-top: 2px;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.list__tools {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
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
  gap: 6px;
  border: none;
  background: transparent;
  border-radius: var(--r-full);
  padding: 5px var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
}

.segmented__btn span {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.segmented__btn--active {
  background: var(--c-surface);
  color: var(--c-ink);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.search {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.search svg {
  position: absolute;
  left: 12px;
  color: var(--c-text-tertiary);
  pointer-events: none;
}

.search input {
  width: 180px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 7px 12px 7px 34px;
  font-size: var(--fs-sm);
  color: var(--c-text);
  background: var(--c-surface);
  outline: none;
}

.select {
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 7px 28px 7px 14px;
  font-size: var(--fs-sm);
  color: var(--c-text);
  background: var(--c-surface)
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239aa1ac' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E")
    no-repeat right 10px center;
  outline: none;
  appearance: none;
}

.search input:focus,
.select:focus {
  border-color: var(--c-primary-border);
}

.list__table-wrap {
  overflow-x: auto;
}

.table {
  width: 100%;
  min-width: 620px;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.table th {
  text-align: left;
  font-weight: 600;
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
  padding: var(--sp-2) var(--sp-5);
  background: var(--c-surface-muted);
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}

.table td {
  padding: 11px var(--sp-5);
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}

.table tbody tr:last-child td {
  border-bottom: none;
}

.table tbody tr {
  cursor: pointer;
  transition: background var(--t-fast);
}

.table tbody tr:hover,
.table tbody tr:focus-visible {
  background: var(--c-primary-soft);
  outline: none;
}

.table__abbr {
  font-weight: 600;
  color: var(--c-ink);
}
.table__mobile-code { display: none; }

.table__muted {
  color: var(--c-text-secondary);
}

.table__go {
  width: 32px;
  color: var(--c-text-tertiary);
  text-align: right;
}

.list__pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-5);
  border-top: 1px solid var(--c-border);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.list__pager button {
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  border-radius: var(--r-full);
  padding: 5px var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
}

.list__pager button:hover:not(:disabled) {
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

.list__pager button:disabled {
  opacity: 0.45;
  cursor: default;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

@media (max-width: 1024px) {
  .market__charts {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .kpi:nth-child(3) {
    border-left: none;
  }

  .kpi:nth-child(n + 3) {
    border-top: 1px solid var(--c-border);
  }

  .list__tools,
  .search,
  .search input {
    width: 100%;
  }

  .segmented {
    width: 100%;
  }

  .segmented__btn {
    flex: 1 1 auto;
    padding: 5px 8px;
    gap: 4px;
    white-space: nowrap;
    justify-content: center;
  }

  .market__charts--secondary {
    grid-template-columns: 1fr;
  }

  .table { min-width: 0; }
  .table__code, .table__extra, .table__go { display: none; }
  .table th, .table td { padding: 12px 10px; }
  .table td.table__abbr { white-space: normal; overflow-wrap: anywhere; }
  .table__mobile-code { display: block; margin-top: 3px; color: var(--c-text-tertiary); font-weight: 400; }
}

@media (max-width: 480px) {
  .kpi {
    padding: var(--sp-3) var(--sp-4);
  }

  .kpi__value {
    font-size: var(--fs-xl);
  }
}
</style>
