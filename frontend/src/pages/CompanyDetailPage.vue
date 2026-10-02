<script setup lang="ts">
import { advisorEnabled } from "@/features";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { EChartsOption } from "echarts";

import AppBadge from "@/components/ui/AppBadge.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCard from "@/components/ui/AppCard.vue";
import AppEmpty from "@/components/ui/AppEmpty.vue";
import AppSection from "@/components/ui/AppSection.vue";
import EChart from "@/components/market/EChart.vue";
import PortalShell from "@/components/layout/PortalShell.vue";
import { IconChevronRight, IconSpark } from "@/components/ui/icons";
import ReportDetailModal from "@/components/report/ReportDetailModal.vue";
import {
  AXIS_STYLE,
  GRID_STYLE,
  LEGEND_STYLE,
  PALETTE,
  TOOLTIP_STYLE,
  axisPct,
  axisYi,
  fmtChartPct,
  fmtChartYi,
  fmtPct,
  fmtYi,
  toNum,
  toYi,
} from "@/components/market/chartTheme";
import {
  addWatch,
  fetchAdvisorReport,
  fetchCompanyDetail,
  fetchCompanySeries,
  fetchDiagnosis,
  fetchInstitutionView,
  fetchReportDetail,
  fetchWatchedCodes,
  removeWatch,
  requestAdvisorReport,
} from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { CompanyDetail, CompanySeries } from "@/types/market";
import type { AdvisorDiagnosis, ResearchReportItem } from "@/types/platform";
import { renderMarkdown } from "@/utils/markdown";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const detail = ref<CompanyDetail | null>(null);
const series = ref<CompanySeries | null>(null);
const loading = ref(true);
const error = ref("");
const watchError = ref("");
const chartError = ref("");

const stockCode = computed(() => String(route.params.code ?? ""));
const imported = computed(() => detail.value?.profile.dataStatus === "imported");

/* 作品说明：自选关注 */

const watched = ref(false);
const watchBusy = ref(false);

async function loadWatched() {
  if (!auth.isAuthenticated) return;
  const code = stockCode.value;
  try {
    const codes = await fetchWatchedCodes();
    if (code === stockCode.value) watched.value = codes.includes(code);
  } catch {
    // 作品说明：关注状态查询失败时保留页面，避免阻断公司详情。
  }
}

async function toggleWatch() {
  if (!auth.isAuthenticated) {
    router.push({ name: "login", query: { redirect: route.fullPath } });
    return;
  }
  watchBusy.value = true;
  watchError.value = "";
  try {
    if (watched.value) {
      await removeWatch(stockCode.value);
      watched.value = false;
    } else {
      await addWatch(stockCode.value);
      watched.value = true;
    }
  } catch (e) {
    watchError.value = e instanceof Error ? e.message : "关注操作失败，请重试。";
  } finally {
    watchBusy.value = false;
  }
}

/* 作品说明：机构观点 */

const institutionReports = ref<ResearchReportItem[]>([]);
const selectedReport = ref<ResearchReportItem | null>(null);

async function loadInstitutionView() {
  const code = stockCode.value;
  try {
    const reports = await fetchInstitutionView(code);
    if (code === stockCode.value) institutionReports.value = reports;
  } catch {
    if (code === stockCode.value) institutionReports.value = [];
  }
}

async function openReport(report: ResearchReportItem) {
  try {
    selectedReport.value = await fetchReportDetail(report.id);
  } catch {
    selectedReport.value = report;
  }
}

/* 作品说明：财务画像 */

const diagnosis = ref<AdvisorDiagnosis | null>(null);
const diagnosisError = ref("");
const reportBusy = ref(false);
let pollTimer: number | null = null;

const reportHtml = computed(() => {
  const md = diagnosis.value?.report?.reportMd;
  return md ? renderMarkdown(md) : "";
});

async function loadDiagnosis() {
  if (!advisorEnabled || !auth.isAuthenticated || !imported.value) return;
  diagnosisError.value = "";
  try {
    diagnosis.value = await fetchDiagnosis(stockCode.value);
    if (diagnosis.value.report?.status === "GENERATING") {
      schedulePoll(diagnosis.value.report.id);
    }
  } catch (e) {
    diagnosisError.value = e instanceof Error ? e.message : "诊断加载失败";
  }
}

async function generateReport() {
  if (!diagnosis.value) return;
  reportBusy.value = true;
  diagnosisError.value = "";
  try {
    const { reportId } = await requestAdvisorReport(stockCode.value);
    if (diagnosis.value.report == null || diagnosis.value.report.id !== reportId) {
      await loadDiagnosis();
    }
    schedulePoll(reportId);
  } catch (e) {
    diagnosisError.value = e instanceof Error ? e.message : "触发失败，请稍后重试";
  } finally {
    reportBusy.value = false;
  }
}

function schedulePoll(reportId: number) {
  stopPoll();
  pollTimer = window.setInterval(async () => {
    try {
      const report = await fetchAdvisorReport(reportId);
      if (diagnosis.value) {
        diagnosis.value.report = report;
      }
      if (report.status !== "GENERATING") {
        stopPoll();
      }
    } catch {
      stopPoll();
    }
  }, 4000);
}

function stopPoll() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

onBeforeUnmount(stopPoll);

const riskLabels: Record<string, string> = {
  conservative: "保守型",
  balanced: "稳健型",
  aggressive: "进取型",
};

/* 作品说明：加载 */

let loadVersion = 0;
async function loadCharts(code = stockCode.value) {
  chartError.value = "";
  try {
    const result = await fetchCompanySeries(code);
    if (code === stockCode.value) series.value = result;
  } catch {
    if (code === stockCode.value) chartError.value = "财务图表暂时加载失败，可以重试或查阅财报原件。";
  }
}

async function load() {
  const version = ++loadVersion;
  const code = stockCode.value;
  loading.value = true;
  error.value = "";
  detail.value = null;
  series.value = null;
  diagnosis.value = null;
  watched.value = false;
  watchError.value = "";
  chartError.value = "";
  institutionReports.value = [];
  stopPoll();
  try {
    const result = await fetchCompanyDetail(code);
    if (version !== loadVersion) return;
    detail.value = result;
    if (detail.value.profile.dataStatus === "imported") {
      void loadCharts(code);
    }
    void loadWatched();
    void loadInstitutionView();
    void loadDiagnosis();
  } catch (e) {
    if (version === loadVersion) error.value = e instanceof Error ? e.message : "加载失败，请稍后重试";
  } finally {
    if (version === loadVersion) loading.value = false;
  }
}

onMounted(load);
watch(stockCode, load);

/* 作品说明：锚点导航 */

const sections = computed(() => {
  const list: { id: string; label: string }[] = [];
  if (imported.value) list.push({ id: "metrics", label: "核心指标" });
  if (imported.value) list.push({ id: "ask", label: "带着问题核对" });
  if (imported.value) list.push({ id: "charts", label: "财务图表" });
  if (institutionReports.value.length) list.push({ id: "research", label: "机构观点" });
  if (advisorEnabled && imported.value) list.push({ id: "advisor", label: "财务画像" });
  return list;
});

const activeSection = ref("");

function scrollTo(id: string) {
  activeSection.value = id;
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

let sectionFrame = 0;
function updateActiveSection() {
  const nodes = sections.value.map((item) => document.getElementById(item.id)).filter((node): node is HTMLElement => !!node);
  const passed = nodes.filter((node) => node.getBoundingClientRect().top <= 140);
  activeSection.value = passed[passed.length - 1]?.id ?? nodes[0]?.id ?? "";
}
function onPageScroll() {
  cancelAnimationFrame(sectionFrame);
  sectionFrame = requestAnimationFrame(updateActiveSection);
}
watch(sections, updateActiveSection, { flush: "post" });
onMounted(() => { window.addEventListener("scroll", onPageScroll, { passive: true }); window.addEventListener("resize", onPageScroll); });
onBeforeUnmount(() => { loadVersion++; cancelAnimationFrame(sectionFrame); window.removeEventListener("scroll", onPageScroll); window.removeEventListener("resize", onPageScroll); });

/** 作品说明：主数据未登记的字段不渲染空标签 */
const profileTags = computed(() => {
  const p = detail.value?.profile;
  if (!p) return [];
  return [
    p.exchange,
    p.board.replace(/A股$/, ""),
    p.industry.replace(/^.*-/, ""),
    p.region,
  ].filter(Boolean);
});

const periodRange = computed(() => {
  const p = detail.value?.profile;
  if (!p?.firstYear) return "";
  return p.firstYear === p.lastYear ? `${p.firstYear}` : `${p.firstYear}–${p.lastYear}`;
});

const periodLabels: Record<string, string> = { FY: "年报", HY: "半年报", Q1: "一季报", Q3: "三季报" };

function coveragePeriods(periods: unknown): string[] {
  return String(periods ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => periodLabels[p] ?? p);
}

/* 作品说明：指标卡 */

const metricCards = computed(() => {
  const core = detail.value?.latestCore ?? {};
  return [
    { label: "营业收入", value: fmtYi(core.revenue), delta: toNum(core.revenue_yoy) },
    { label: "净利润", value: fmtYi(core.net_profit), delta: toNum(core.net_profit_yoy) },
    { label: "扣非净利润", value: fmtYi(core.net_profit_excl), delta: toNum(core.net_profit_excl_yoy) },
    { label: "ROE", value: fmtPct(core.roe), delta: null },
    { label: "毛利率", value: fmtPct(core.gross_profit_margin), delta: null },
    { label: "每股收益", value: core.eps == null ? "—" : `${core.eps} 元`, delta: null },
  ];
});

/* 作品说明：图表 */

const profitOption = computed<EChartsOption>(() => {
  const rows = series.value?.core ?? [];
  return {
    color: PALETTE,
    tooltip: TOOLTIP_STYLE,
    legend: { ...LEGEND_STYLE, top: 0 },
    grid: { ...GRID_STYLE, top: 50 },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: [
      {
        type: "value",
        name: "金额",
        ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisYi },
      },
      {
        type: "value",
        name: "同比",
        ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisPct },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "营业收入",
        type: "bar",
        data: rows.map((r) => toYi(r.revenue)),
        barWidth: 26,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
      {
        name: "净利润",
        type: "bar",
        data: rows.map((r) => toYi(r.net_profit)),
        barWidth: 26,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
      {
        name: "净利润同比",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        symbolSize: 7,
        data: rows.map((r) => toNum(r.net_profit_yoy)),
        tooltip: { valueFormatter: fmtChartPct },
      },
    ],
  };
});

const marginOption = computed<EChartsOption>(() => {
  const rows = series.value?.core ?? [];
  const line = (name: string, key: string) => ({
    name,
    type: "line" as const,
    smooth: true,
    symbolSize: 7,
    data: rows.map((r) => toNum(r[key])),
    tooltip: { valueFormatter: fmtChartPct },
  });
  return {
    color: PALETTE,
    tooltip: TOOLTIP_STYLE,
    legend: { ...LEGEND_STYLE, top: 0 },
    grid: { ...GRID_STYLE, top: 50 },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: {
      type: "value",
      name: "比例",
      ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisPct },
    },
    series: [
      line("毛利率", "gross_profit_margin"),
      line("净利率", "net_profit_margin"),
      line("ROE", "roe"),
    ],
  };
});

const balanceOption = computed<EChartsOption>(() => {
  const rows = series.value?.balance ?? [];
  return {
    color: PALETTE,
    tooltip: TOOLTIP_STYLE,
    legend: { ...LEGEND_STYLE, top: 0 },
    grid: { ...GRID_STYLE, top: 50 },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: [
      {
        type: "value",
        name: "金额",
        ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisYi },
      },
      {
        type: "value",
        name: "负债率",
        ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisPct },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "总资产",
        type: "bar",
        data: rows.map((r) => toYi(r.total_assets)),
        barWidth: 26,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
      {
        name: "总负债",
        type: "bar",
        data: rows.map((r) => toYi(r.total_liabilities)),
        barWidth: 26,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        tooltip: { valueFormatter: fmtChartYi },
      },
      {
        name: "资产负债率",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        symbolSize: 7,
        data: rows.map((r) => toNum(r.asset_liability_ratio)),
        tooltip: { valueFormatter: fmtChartPct },
      },
    ],
  };
});

const cashflowOption = computed<EChartsOption>(() => {
  const rows = series.value?.cashflow ?? [];
  const bar = (name: string, key: string) => ({
    name,
    type: "bar" as const,
    barWidth: 18,
    itemStyle: { borderRadius: [3, 3, 0, 0] },
    data: rows.map((r) => toYi(r[key])),
    tooltip: { valueFormatter: fmtChartYi },
  });
  return {
    color: [PALETTE[1], PALETTE[2], PALETTE[0]],
    tooltip: TOOLTIP_STYLE,
    legend: { ...LEGEND_STYLE, top: 0 },
    grid: { ...GRID_STYLE, top: 50 },
    xAxis: { type: "category", data: rows.map((r) => String(r.year)), ...AXIS_STYLE },
    yAxis: {
      type: "value",
      name: "现金流",
      ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: axisYi },
    },
    series: [
      bar("经营现金流", "operating_cf"),
      bar("投资现金流", "investing_cf"),
      bar("筹资现金流", "financing_cf"),
    ],
  };
});

/* 作品说明：问 AI 联动 */

const askPresets = computed(() => {
  const abbr = detail.value?.profile.abbr ?? "";
  // 作品说明：用最新年报年份，避免问到只有季报/半年报的年份
  const year = detail.value?.latestCore.year ?? detail.value?.profile.lastYear ?? "";
  return [
    `${abbr}近三年的净利润和营收变化趋势如何？请画图`,
    `${abbr}${year}年的盈利能力怎么样？`,
    `${abbr}业绩变化的原因是什么？`,
  ];
});

function askAi(question: string) {
  router.push({ path: "/workspace", query: { q: question } });
}
</script>

<template>
  <PortalShell>
    <div v-if="error" class="company__error" role="alert">
      {{ error }}
      <router-link to="/market">← 返回公司数据</router-link>
    </div>

    <template v-else-if="detail">
      <nav class="crumbs" aria-label="位置">
        <router-link to="/market">公司数据</router-link>
        <IconChevronRight :size="12" />
        <span>{{ detail.profile.abbr }}</span>
      </nav>

      <!-- 作品说明：档案头 -->
      <section class="head">
        <div class="head__main">
          <div class="head__identity">
            <h1 class="head__name">{{ detail.profile.abbr }}</h1>
            <span class="head__code mono">{{ detail.profile.stockCode }}</span>
            <AppBadge :variant="imported ? 'success' : 'warning'">
              {{ imported ? "已结构化入库" : "财务数据待入库" }}
            </AppBadge>
          </div>
          <p v-if="detail.profile.fullName" class="head__fullname">{{ detail.profile.fullName }}</p>
          <div v-if="profileTags.length || detail.regCapital || detail.employees" class="head__tags">
            <AppBadge v-for="tag in profileTags" :key="tag" variant="default">{{ tag }}</AppBadge>
            <span v-if="detail.regCapital" class="head__meta">注册资本 {{ detail.regCapital }}</span>
            <span v-if="detail.employees" class="head__meta">雇员 {{ detail.employees }} 人</span>
          </div>
        </div>
        <div class="head__actions">
          <router-link class="head__materials" :to="{path: '/reports', query: {tab: 'financial', company: stockCode}}">查阅财报</router-link>
          <AppButton
            :variant="watched ? 'secondary' : 'outline'"
            size="sm"
            :disabled="watchBusy"
            @click="toggleWatch"
          >
            {{ watched ? "★ 已关注" : "☆ 关注" }}
          </AppButton>
          <AppButton v-if="imported" size="sm" @click="scrollTo('ask')">
            <IconSpark :size="14" />
            提问核对
          </AppButton>
        </div>

        <div v-if="imported && detail.coverage?.length" class="head__coverage">
          <span class="head__coverage-label">
            可问答报告期<template v-if="periodRange"> · {{ periodRange }}</template>
          </span>
          <div class="head__coverage-list">
            <span v-for="row in detail.coverage" :key="String(row.year)" class="period">
              <strong>{{ row.year }}</strong>
              <span>{{ coveragePeriods(row.periods).join(" · ") }}</span>
            </span>
          </div>
        </div>
        <p v-if="watchError" class="head__error" role="alert">{{ watchError }}</p>
      </section>

      <!-- 作品说明：待入库 -->
      <div v-if="!imported" class="pending" role="status">
        <strong>财务数据待入库</strong>
        <span>
          该公司已收录基本信息{{ detail.profile.datasetTag ? `（数据集：${detail.profile.datasetTag}）` : "" }}。管理员导入并通过发布检查后，这里会显示核心指标与图表；在此之前，数字问答不会回答该公司的财务数字。
        </span>
      </div>

      <!-- 作品说明：分区导航 -->
      <nav v-if="sections.length > 1" class="tabs" aria-label="页面分区">
        <button
          v-for="item in sections"
          :key="item.id"
          type="button"
          class="tabs__btn"
          :class="{ 'tabs__btn--active': activeSection === item.id }"
          :aria-current="activeSection === item.id ? 'location' : undefined"
          @click="scrollTo(item.id)"
        >
          {{ item.label }}
        </button>
      </nav>

      <!-- 作品说明：核心指标 -->
      <section v-if="imported" id="metrics" class="block">
        <div class="block__head">
          <h2>核心指标</h2>
          <p>
            <template v-if="detail.latestCore.year">{{ detail.latestCore.year }} 年报口径 · </template>
            同比为较上年变动，红涨绿跌
          </p>
        </div>
        <div class="metrics">
          <div v-for="card in metricCards" :key="card.label" class="metric">
            <span class="metric__label">{{ card.label }}</span>
            <span class="metric__value">{{ card.value }}</span>
            <span
              v-if="card.delta !== null"
              class="metric__delta"
              :class="card.delta >= 0 ? 'metric__delta--up' : 'metric__delta--down'"
            >
              {{ card.delta >= 0 ? "▲" : "▼" }} {{ Math.abs(card.delta).toFixed(2) }}%
            </span>
            <span v-else class="metric__delta metric__delta--none">—</span>
          </div>
        </div>
      </section>

      <!-- 作品说明：提问核对 -->
      <section v-if="imported" id="ask" class="ask">
        <div class="ask__text">
          <h2>带着问题核对</h2>
          <p>问题会带到 AI 工作台，回答附查询 SQL、图表和原始财报页码；证据不足时会明确说明。</p>
        </div>
        <div class="ask__list">
          <button
            v-for="question in askPresets"
            :key="question"
            type="button"
            class="ask__btn"
            @click="askAi(question)"
          >
            <span>{{ question }}</span>
            <IconChevronRight :size="14" />
          </button>
        </div>
      </section>

      <!-- 作品说明：图表 -->
      <section v-if="imported" id="charts" class="block">
        <div class="block__head">
          <h2>财务图表</h2>
          <p>数据来自已发布的年报；缺失年份不连线、不补零</p>
        </div>
        <div v-if="chartError" class="pending" role="alert">{{ chartError }}<AppButton variant="outline" size="sm" @click="loadCharts()">重新加载图表</AppButton></div>
        <p v-else-if="!series" role="status">正在加载财务图表…</p>
        <div v-else class="charts">
          <AppCard>
            <template #header>
              <AppSection title="营收与净利润" description="柱：亿元 · 线：净利润同比（%）" dense />
            </template>
            <EChart :option="profitOption" height="300px" />
          </AppCard>
          <AppCard>
            <template #header>
              <AppSection title="盈利能力" description="单位：%" dense />
            </template>
            <EChart :option="marginOption" height="300px" />
          </AppCard>
          <AppCard>
            <template #header>
              <AppSection title="资产负债结构" description="柱：亿元 · 线：资产负债率（%）" dense />
            </template>
            <EChart :option="balanceOption" height="300px" />
          </AppCard>
          <AppCard>
            <template #header>
              <AppSection title="现金流" description="单位：亿元" dense />
            </template>
            <EChart :option="cashflowOption" height="300px" />
          </AppCard>
        </div>
      </section>

      <!-- 作品说明：机构观点 -->
      <section v-if="institutionReports.length" id="research" class="block">
        <div class="block__head">
          <h2>机构观点</h2>
          <p>{{ institutionReports.length }} 篇相关研报 · 观点来自第三方机构，仅供学习对照</p>
        </div>
        <div class="iview__list">
          <article
            v-for="report in institutionReports"
            :key="report.id"
            class="iview__item"
            @click="openReport(report)"
          >
            <div class="iview__row">
              <AppBadge :variant="report.rating?.includes('买') ? 'danger' : 'default'">
                {{ report.rating || "未评级" }}
              </AppBadge>
              <span class="iview__org">{{ report.orgSname || report.orgName }}</span>
              <span class="iview__date mono">{{ report.publishDate ?? "" }}</span>
            </div>
            <p class="iview__title">{{ report.title }}</p>
            <div class="iview__meta">
              <span v-if="report.predictThisYearEps">预测 EPS {{ report.predictThisYearEps }}</span>
              <span v-if="report.predictThisYearPe">预测 PE {{ report.predictThisYearPe }}</span>
              <span v-if="report.aimPrice">目标价 {{ report.aimPrice }}</span>
              <span v-if="report.researcher">{{ report.researcher }}</span>
            </div>
          </article>
        </div>
      </section>

      <!-- 作品说明：财务画像（学习用途） -->
      <section v-if="advisorEnabled && imported" id="advisor">
        <AppCard padding="lg">
          <template #header>
            <AppSection title="财务画像（学习用途）" description="规则评分 + 财务指标解读" dense />
          </template>

          <div v-if="!auth.isAuthenticated" class="advisor__guest">
            <router-link :to="{ name: 'login', query: { redirect: route.fullPath } }">登录</router-link>
            后查看规则评分与个性化 AI 诊断报告
          </div>

          <template v-else-if="diagnosis">
            <div class="advisor__layout">
              <div class="advisor__score">
                <span class="advisor__score-value">{{ diagnosis.total }}</span>
                <span class="advisor__score-rating">{{ diagnosis.rating }}</span>
                <span class="advisor__score-sub">基于 {{ diagnosis.latestYear }} 年报 · 满分 100</span>
              </div>
              <div class="advisor__dims">
                <div v-for="dim in diagnosis.dimensions" :key="dim.name" class="advisor__dim">
                  <div class="advisor__dim-head">
                    <span>{{ dim.name }}</span>
                    <span class="mono">{{ dim.score }} / {{ dim.max }}</span>
                  </div>
                  <div class="advisor__dim-bar">
                    <div class="advisor__dim-fill" :style="{ width: `${(dim.score / dim.max) * 100}%` }" />
                  </div>
                  <p class="advisor__dim-comment">{{ dim.comment }}</p>
                </div>
              </div>
            </div>

            <div class="advisor__report">
              <div class="advisor__report-head">
                <h4>
                  个性化诊断报告
                  <span v-if="auth.user" class="advisor__risk-tag">
                    {{ riskLabels[auth.user.riskProfile] ?? auth.user.riskProfile }}
                  </span>
                </h4>
                <AppButton
                  size="sm"
                  :loading="reportBusy || diagnosis.report?.status === 'GENERATING'"
                  @click="generateReport"
                >
                  {{ diagnosis.report?.status === "GENERATING" ? "生成中…" : diagnosis.report ? "重新生成" : "生成 AI 报告" }}
                </AppButton>
              </div>
              <p v-if="diagnosisError" class="advisor__error">{{ diagnosisError }}</p>
              <div v-if="diagnosis.report?.status === 'GENERATING'" class="advisor__generating">
                报告生成中，完成后将通过站内信通知，也可停留本页等待自动刷新…
              </div>
              <div
                v-else-if="diagnosis.report?.status === 'READY' && reportHtml"
                class="advisor__md"
                v-html="reportHtml"
              />
              <div v-else-if="diagnosis.report?.status === 'FAILED'" class="advisor__error">
                上次生成失败：{{ diagnosis.report.reportMd || "请重试" }}
              </div>
              <p class="advisor__disclaimer">
                评分由规则引擎基于已入库年报数据计算，报告由 AI 生成，仅供参考，不构成投资建议。
              </p>
            </div>
          </template>

          <div v-else-if="diagnosisError" class="advisor__error">{{ diagnosisError }}</div>
          <div v-else class="advisor__guest">诊断加载中…</div>
        </AppCard>
      </section>

    </template>

    <AppEmpty v-else-if="loading" title="加载中…" description="正在获取公司详情" />

    <ReportDetailModal :report="selectedReport" @close="selectedReport = null" />
  </PortalShell>
</template>

<style scoped>
.company__error {
  padding: var(--sp-6);
  text-align: center;
  color: var(--c-danger);
  background: var(--c-danger-soft);
  border-radius: var(--r-lg);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.crumbs {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  margin-bottom: calc(var(--sp-3) - var(--portal-content-gap));
  font-size: var(--fs-sm);
  color: var(--c-text-tertiary);
}

.crumbs a {
  color: var(--c-text-secondary);
}

.crumbs a:hover {
  color: var(--c-primary);
  text-decoration: none;
}

/* 作品说明：档案头 */
.head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--sp-4) var(--sp-6);
  padding: var(--sp-6);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.head__main {
  min-width: 0;
}

.head__identity {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-3);
}

.head__name {
  font-size: var(--fs-2xl);
  font-weight: 800;
  line-height: 1.2;
  color: var(--c-ink);
}

.head__code {
  font-size: var(--fs-md);
  color: var(--c-text-tertiary);
}

.head__fullname {
  margin-top: var(--sp-1);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.head__tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}

.head__meta {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.head__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--sp-2);
}
.head__materials { display: inline-flex; align-items: center; min-height: var(--btn-height-sm); padding: 0 var(--sp-3); border: 1px solid var(--c-border-strong); border-radius: var(--r-full); background: var(--c-surface); font-size: var(--fs-sm); }
.head__error { grid-column: 1 / -1; color: var(--c-danger); font-size: var(--fs-sm); }

.head__coverage {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-4);
  padding-top: var(--sp-4);
  border-top: 1px solid var(--c-border);
}

.head__coverage-label {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--c-text-secondary);
}

.head__coverage-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.period {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 3px var(--sp-3);
  border-radius: var(--r-full);
  background: var(--c-surface-muted);
  border: 1px solid var(--c-border);
  font-size: var(--fs-xs);
}

.period strong {
  font-family: var(--font-mono);
  color: var(--c-ink);
}

.period span {
  color: var(--c-text-tertiary);
}

/* 作品说明：待入库提示 */
.pending {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--sp-4) var(--sp-5);
  border: 1px solid #f1dcae;
  border-radius: var(--r-lg);
  background: var(--c-warning-soft);
  font-size: var(--fs-sm);
  line-height: 1.7;
  color: var(--c-text-secondary);
}

.pending strong {
  color: var(--c-warning);
}

/* 作品说明：分区导航 */
.tabs {
  position: sticky;
  top: var(--portal-nav-h);
  z-index: 10;
  display: flex;
  gap: var(--sp-1);
  margin: calc(var(--sp-2) - var(--portal-content-gap)) 0 calc(var(--sp-2) - var(--portal-content-gap));
  padding: var(--sp-2) 0;
  background: var(--c-bg);
  overflow-x: auto;
  scrollbar-width: none;
}

.tabs::-webkit-scrollbar {
  display: none;
}

.tabs__btn {
  flex: 0 0 auto;
  border: 1px solid transparent;
  background: transparent;
  border-radius: var(--r-full);
  padding: 6px var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: background var(--t-fast), color var(--t-fast);
}

.tabs__btn:hover {
  color: var(--c-ink);
  background: var(--c-surface);
  border-color: var(--c-border);
}

.tabs__btn--active {
  color: var(--c-primary);
  background: var(--c-primary-soft);
  border-color: var(--c-primary-border);
}

/* 作品说明：通用分区 */
.block,
#ask,
#advisor {
  scroll-margin-top: calc(var(--portal-nav-h) + 56px);
}

.block__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  margin-bottom: var(--sp-3);
}

.block__head h2 {
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-ink);
}

.block__head p {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

/* 作品说明：指标 */
.metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: var(--sp-4) var(--sp-5);
}

.metric + .metric {
  border-left: 1px solid var(--c-border);
}

.metric__label {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.metric__value {
  font-size: var(--fs-xl);
  font-weight: 800;
  letter-spacing: -0.3px;
  line-height: 1.25;
  color: var(--c-ink);
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}

.metric__delta {
  font-size: var(--fs-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.metric__delta--up {
  color: var(--c-danger);
}

.metric__delta--down {
  color: var(--c-success);
}

.metric__delta--none {
  color: var(--c-text-tertiary);
  font-weight: 400;
}

/* 作品说明：提问核对 */
.ask {
  display: grid;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 2fr);
  gap: var(--sp-5);
  align-items: center;
  padding: var(--sp-5) var(--sp-6);
  background: var(--c-primary-soft);
  border: 1px solid var(--c-primary-border);
  border-radius: var(--r-lg);
}

.ask__text h2 {
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-ink);
}

.ask__text p {
  margin-top: var(--sp-1);
  font-size: var(--fs-xs);
  line-height: 1.7;
  color: var(--c-text-secondary);
}

.ask__list {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.ask__btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  width: 100%;
  border: 1px solid var(--c-primary-border);
  background: var(--c-surface);
  color: var(--c-ink);
  border-radius: var(--r-md);
  padding: 10px var(--sp-4);
  font-size: var(--fs-sm);
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--t-fast), color var(--t-fast);
}

.ask__btn svg {
  flex-shrink: 0;
  color: var(--c-primary);
}

.ask__btn:hover {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

/* 作品说明：图表 */
.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

/* 作品说明：机构观点 */
.iview__list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-3);
}

.iview__item {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  cursor: pointer;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

.iview__item:hover {
  border-color: var(--c-primary-border);
  box-shadow: var(--shadow-md);
}

.iview__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.iview__org {
  font-size: var(--fs-sm);
  font-weight: 600;
}

.iview__date {
  margin-left: auto;
  color: var(--c-text-tertiary);
}

.iview__title {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-ink);
  line-height: 1.55;
}

.iview__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

/* 作品说明：财务画像 */
.advisor__guest {
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
  padding: var(--sp-3) 0;
}

.advisor__guest a {
  color: var(--c-primary);
  font-weight: 500;
}

.advisor__layout {
  display: flex;
  gap: var(--sp-6);
  align-items: stretch;
  flex-wrap: wrap;
}

.advisor__score {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--sp-1);
  min-width: 150px;
  padding: var(--sp-5);
  background: var(--c-primary-soft);
  border-radius: var(--r-xl);
}

.advisor__score-value {
  font-size: 48px;
  font-weight: 800;
  color: var(--c-primary);
  line-height: 1;
}

.advisor__score-rating {
  font-size: var(--fs-md);
  font-weight: 700;
}

.advisor__score-sub {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.advisor__dims {
  flex: 1;
  min-width: 260px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3) var(--sp-5);
}

.advisor__dim-head {
  display: flex;
  justify-content: space-between;
  font-size: var(--fs-sm);
  margin-bottom: 4px;
}

.advisor__dim-bar {
  height: 6px;
  background: var(--c-bg);
  border-radius: var(--r-full);
  overflow: hidden;
}

.advisor__dim-fill {
  height: 100%;
  background: var(--c-primary);
  border-radius: var(--r-full);
  transition: width var(--t-base);
}

.advisor__dim-comment {
  margin-top: 4px;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.advisor__report {
  margin-top: var(--sp-5);
  border-top: 1px dashed var(--c-border);
  padding-top: var(--sp-4);
}

.advisor__report-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

.advisor__report-head h4 {
  font-size: var(--fs-md);
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.advisor__risk-tag {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  font-size: var(--fs-xs);
  font-weight: 400;
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 2px 10px;
  color: var(--c-text-secondary);
  white-space: nowrap;
}

.advisor__generating {
  margin-top: var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  background: var(--c-bg);
  border-radius: var(--r-md);
  padding: var(--sp-3) var(--sp-4);
}

.advisor__md {
  margin-top: var(--sp-3);
  font-size: var(--fs-sm);
  line-height: 1.75;
  color: var(--c-text);
}

.advisor__md :deep(h2) {
  font-size: var(--fs-md);
  font-weight: 700;
  margin: var(--sp-4) 0 var(--sp-2);
}

.advisor__md :deep(h3) {
  font-size: var(--fs-sm);
  font-weight: 600;
  margin: var(--sp-3) 0 var(--sp-1);
}

.advisor__md :deep(p),
.advisor__md :deep(li) {
  margin: var(--sp-1) 0;
}

.advisor__md :deep(ul),
.advisor__md :deep(ol) {
  padding-left: var(--sp-5);
}

.advisor__error {
  margin-top: var(--sp-3);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}

.advisor__disclaimer {
  margin-top: var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

@media (max-width: 1100px) {
  .metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .metric:nth-child(4) {
    border-left: none;
  }

  .metric:nth-child(n + 4) {
    border-top: 1px solid var(--c-border);
  }
}

@media (max-width: 900px) {
  .charts,
  .ask {
    grid-template-columns: 1fr;
  }

  .advisor__dims {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .head {
    grid-template-columns: 1fr;
    padding: var(--sp-5);
  }

  .head__name {
    font-size: var(--fs-xl);
  }

  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric {
    padding: var(--sp-3) var(--sp-4);
  }

  .metric:nth-child(n) {
    border-left: none;
    border-top: none;
  }

  .metric:nth-child(even) {
    border-left: 1px solid var(--c-border);
  }

  .metric:nth-child(n + 3) {
    border-top: 1px solid var(--c-border);
  }

  .ask {
    padding: var(--sp-4);
  }

  .iview__list {
    grid-template-columns: 1fr;
  }

  .advisor__layout,
  .advisor__report-head {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
