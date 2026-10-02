<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import AppBadge from "@/components/ui/AppBadge.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCard from "@/components/ui/AppCard.vue";
import AppEmpty from "@/components/ui/AppEmpty.vue";
import AppSection from "@/components/ui/AppSection.vue";
import PortalNav from "@/components/market/PortalNav.vue";
import {
  IconAlert,
  IconChart,
  IconDatabase,
  IconList,
  IconRefresh,
  IconSpark,
} from "@/components/ui/icons";
import { parseEtlSteps } from "@/utils/etl";
import {
  fetchAdminChatLogs,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchEtlTasks,
  fetchHealth,
  updateUserStatus,
  uploadEtlFile,
  retryEtlTask,
} from "@/services/api";
import type { AdminOverview, AdminUserItem, ChatLogItem, EtlTaskItem } from "@/types/platform";

type Tab = "overview" | "etl" | "users" | "logs";
const tab = ref<Tab>("overview");

const tabs = [
  { key: "overview", label: "运营看板" },
  { key: "etl", label: "数据管线" },
  { key: "users", label: "用户管理" },
  { key: "logs", label: "对话审计" },
] as const;

/* 作品说明：看板 */

const overview = ref<AdminOverview | null>(null);
const overviewLoading = ref(true);
const overviewError = ref("");

async function loadOverview() {
  overviewLoading.value = true;
  overviewError.value = "";
  try {
    overview.value = await fetchAdminOverview();
  } catch (error) {
    overviewError.value = error instanceof Error ? error.message : "运营数据加载失败，请稍后重试。";
  } finally {
    overviewLoading.value = false;
  }
}

const overviewStats = computed(() => [
  {
    label: "注册用户",
    value: overview.value?.userCount ?? 0,
    helper: "平台累计账户",
    icon: IconList,
    tone: "primary",
  },
  {
    label: "会话总数",
    value: overview.value?.sessionCount ?? 0,
    helper: "已保存的问答会话",
    icon: IconSpark,
    tone: "violet",
  },
  {
    label: "累计对话",
    value: overview.value?.chatTotal ?? 0,
    helper: "已写入审计记录",
    icon: IconDatabase,
    tone: "teal",
  },
  {
    label: "今日对话",
    value: overview.value?.chatToday ?? 0,
    helper: `门户访客 ${overview.value?.uvToday ?? 0} 人`,
    icon: IconChart,
    tone: "orange",
  },
]);

const etlBreakdown = computed(() => [
  { key: "success", label: "成功", value: overview.value?.etlStats.success ?? 0, tone: "success" },
  { key: "running", label: "处理中", value: overview.value?.etlStats.running ?? 0, tone: "primary" },
  { key: "pending", label: "排队", value: overview.value?.etlStats.pending ?? 0, tone: "warning" },
  { key: "partial", label: "部分完成", value: overview.value?.etlStats.partial ?? 0, tone: "warning" },
  { key: "failed", label: "失败", value: overview.value?.etlStats.failed ?? 0, tone: "danger" },
]);

const etlTotal = computed(() => etlBreakdown.value.reduce((sum, item) => sum + item.value, 0));
const etlSuccessRate = computed(() => {
  if (!etlTotal.value) return 0;
  return Math.round(((overview.value?.etlStats.success ?? 0) / etlTotal.value) * 100);
});
const etlAttention = computed(() => {
  const stats = overview.value?.etlStats ?? {};
  if ((stats.failed ?? 0) > 0) return `${stats.failed} 个失败任务需要处理`;
  if ((stats.partial ?? 0) > 0) return `${stats.partial} 个任务需要补充处理`;
  if ((stats.running ?? 0) + (stats.pending ?? 0) > 0) return "数据管线正在工作";
  if (etlTotal.value > 0) return "当前任务均已结束";
  return "暂时没有导入任务";
});

function formatCount(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

/* 作品说明：ETL 任务 */

const tasks = ref<EtlTaskItem[]>([]);
const taskTotal = ref(0);
const taskPage = ref(1);
const taskStats = ref<Record<string, number>>({});
const uploading = ref(false);
const retryingTask = ref<number | null>(null);
const uploadMessage = ref("");
const ocrCapability = ref("正在检查财报导入能力…");
onMounted(async () => {
  try {
    const health = await fetchHealth();
    ocrCapability.value = health.ocr?.detail || "导入能力未知，请检查 OCR 配置或本地缓存。";
  } catch {
    ocrCapability.value = "无法检查导入能力，请确认 AI 服务可用后再上传。";
  }
});
const fileType = ref<"financial" | "research">("financial");
const fileInput = ref<HTMLInputElement | null>(null);
let taskTimer: number | null = null;

async function loadTasks() {
  const result = await fetchEtlTasks({ page: taskPage.value, size: 10 });
  tasks.value = result.records;
  taskTotal.value = result.total;
  taskStats.value = (result.stats as Record<string, number>) ?? {};
  const active = tasks.value.some((t) => t.status === "PENDING" || t.status === "RUNNING");
  stopTaskPoll();
  if (active) {
    taskTimer = window.setTimeout(() => void loadTasks(), 5000);
  }
}

function stopTaskPoll() {
  if (taskTimer !== null) {
    window.clearTimeout(taskTimer);
    taskTimer = null;
  }
}

onBeforeUnmount(stopTaskPoll);

async function onUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  uploading.value = true;
  uploadMessage.value = "";
  try {
    const task = await uploadEtlFile(file, fileType.value);
    uploadMessage.value = `已入队：${task.fileName}（任务 #${task.id}）`;
    taskPage.value = 1;
    await loadTasks();
  } catch (e) {
    uploadMessage.value = e instanceof Error ? e.message : "上传失败";
  } finally {
    uploading.value = false;
    input.value = "";
  }
}

function stepSummary(task: EtlTaskItem): string {
  const { steps, retryable } = parseEtlSteps(task.stepsJson);
  return steps.map((s) => `${s.name}:${s.status}`).join(" → ") + (retryable.length ? ` · 待重试阶段：${retryable.join("、")}` : "");
}

async function retryTask(task: EtlTaskItem) {
  if (retryingTask.value !== null) return;
  retryingTask.value = task.id;
  uploadMessage.value = "";
  try { await retryEtlTask(task.id); await loadTasks(); }
  catch (e) { uploadMessage.value = e instanceof Error ? e.message : "重试失败"; }
  finally { retryingTask.value = null; }
}

/* 作品说明：用户 */

const users = ref<AdminUserItem[]>([]);
const userTotal = ref(0);
const userPage = ref(1);
const userKeyword = ref("");

async function loadUsers() {
  const result = await fetchAdminUsers({
    page: userPage.value,
    size: 10,
    keyword: userKeyword.value || undefined,
  });
  users.value = result.records;
  userTotal.value = result.total;
}

async function toggleUser(user: AdminUserItem) {
  await updateUserStatus(user.id, user.status === 1 ? 0 : 1);
  user.status = user.status === 1 ? 0 : 1;
}

/* 作品说明：审计日志 */

const logs = ref<ChatLogItem[]>([]);
const logTotal = ref(0);
const logPage = ref(1);

async function loadLogs() {
  const result = await fetchAdminChatLogs(logPage.value, 12);
  logs.value = result.records;
  logTotal.value = result.total;
}

/* 作品说明：tab 切换加载 */

const loaders: Record<Tab, () => Promise<void>> = {
  overview: loadOverview,
  etl: loadTasks,
  users: loadUsers,
  logs: loadLogs,
};

watch(tab, () => void loaders[tab.value]());
watch(taskPage, () => void loadTasks());
watch(userPage, () => void loadUsers());
watch(logPage, () => void loadLogs());
onMounted(() => void loadOverview());

const statusLabels: Record<string, string> = {
  PENDING: "排队中",
  RUNNING: "处理中",
  SUCCESS: "成功",
  FAILED: "失败",
  PARTIAL: "部分完成",
};

const badgeVariant = (status: string) => {
  switch (status) {
    case "SUCCESS":
    case "ok":
      return "success";
    case "FAILED":
      return "danger";
    case "RUNNING":
      return "primary";
    case "PENDING":
    case "PARTIAL":
      return "warning";
    default:
      return "default";
  }
};
</script>

<template>
  <div class="admin">
    <PortalNav />

    <main class="admin__main">
      <header class="admin__header">
        <div>
          <span class="admin__eyebrow">ADMIN CONSOLE</span>
          <h1>运营与数据管理</h1>
          <p>查看平台使用情况，管理报告导入、用户和对话审计。</p>
        </div>
        <span class="admin__identity">管理员工作区</span>
      </header>

      <nav class="admin__tabs">
        <button
          v-for="item in tabs"
          :key="item.key"
          type="button"
          class="admin__tab"
          :class="{ 'admin__tab--active': tab === item.key }"
          @click="tab = item.key"
        >
          {{ item.label }}
        </button>
      </nav>

      <!-- 作品说明：运营看板 -->
      <section v-if="tab === 'overview'" class="admin__section admin__section--overview">
        <div class="overview__head">
          <div>
            <span class="overview__kicker">运营看板</span>
            <h2>平台运行概况</h2>
            <p>关键运营数据与报告处理状态，均来自当前服务。</p>
          </div>
          <AppButton variant="outline" size="sm" :loading="overviewLoading" @click="loadOverview">
            <IconRefresh :size="14" />
            刷新数据
          </AppButton>
        </div>

        <div v-if="overviewError" class="overview__error" role="alert">
          <IconAlert :size="18" />
          <span>{{ overviewError }}</span>
          <button type="button" @click="loadOverview">重新加载</button>
        </div>

        <div v-if="!overview && overviewLoading" class="overview__loading-grid" aria-label="正在加载运营数据">
          <div v-for="item in 4" :key="item" class="overview__loading-card">
            <AppSkeleton :lines="2" />
          </div>
        </div>

        <template v-if="overview">
          <div class="overview__metrics">
            <article v-for="stat in overviewStats" :key="stat.label" class="metric-card">
              <div class="metric-card__topline">
                <span class="metric-card__label">{{ stat.label }}</span>
                <span class="metric-card__icon" :class="`metric-card__icon--${stat.tone}`">
                  <component :is="stat.icon" :size="17" />
                </span>
              </div>
              <strong class="metric-card__value">{{ formatCount(stat.value) }}</strong>
              <span class="metric-card__helper">{{ stat.helper }}</span>
            </article>
          </div>

          <div class="overview__panels">
            <article class="overview-card overview-card--pipeline">
              <header class="overview-card__head">
                <div>
                  <span class="overview-card__eyebrow">DATA PIPELINE</span>
                  <h3>报告处理状态</h3>
                  <p>{{ etlAttention }}</p>
                </div>
                <button type="button" class="overview-card__action" @click="tab = 'etl'">查看任务</button>
              </header>

              <div class="pipeline__summary">
                <div>
                  <strong>{{ etlSuccessRate }}%</strong>
                  <span>任务成功率</span>
                </div>
                <p>共处理 {{ etlTotal }} 个导入任务</p>
              </div>
              <div
                class="pipeline__progress"
                role="progressbar"
                aria-label="ETL 任务成功率"
                aria-valuemin="0"
                aria-valuemax="100"
                :aria-valuenow="etlSuccessRate"
              >
                <span :style="{ width: `${etlSuccessRate}%` }" />
              </div>
              <div class="pipeline__breakdown">
                <div v-for="item in etlBreakdown" :key="item.key" class="pipeline__status">
                  <span class="pipeline__dot" :class="`pipeline__dot--${item.tone}`" />
                  <span>{{ item.label }}</span>
                  <strong>{{ item.value }}</strong>
                </div>
              </div>
            </article>

            <article class="overview-card overview-card--hot">
              <header class="overview-card__head">
                <div>
                  <span class="overview-card__eyebrow">MARKET INTEREST</span>
                  <h3>热门公司</h3>
                  <p>按门户访问热度排序</p>
                </div>
                <IconChart :size="20" />
              </header>

              <div v-if="overview.hotCompanies.length" class="hot-list">
                <router-link
                  v-for="(item, idx) in overview.hotCompanies"
                  :key="item.stockCode"
                  :to="`/market/${item.stockCode}`"
                  class="hot-list__item"
                >
                  <span class="hot-list__rank" :class="{ 'hot-list__rank--top': idx < 3 }">{{ idx + 1 }}</span>
                  <span class="hot-list__company">
                    <strong>{{ item.abbr }}</strong>
                    <small>{{ item.stockCode }}</small>
                  </span>
                  <span class="hot-list__views">{{ item.views }} 次访问</span>
                </router-link>
              </div>
              <div v-else class="hot-list__empty">
                <span class="hot-list__empty-icon"><IconChart :size="22" /></span>
                <strong>暂无访问记录</strong>
                <p>用户打开公司详情后，热度排行会显示在这里。</p>
              </div>
            </article>
          </div>
        </template>
      </section>

      <!-- 作品说明：数据管线 -->
      <section v-else-if="tab === 'etl'" class="admin__section">
        <AppCard padding="lg">
          <template #header>
            <AppSection title="上传报告 PDF" description="财报 / 研报 PDF 入队后由 Python 管线异步处理" dense />
          </template>
          <p class="admin__hint">
            上传后任务入队（RabbitMQ），Python 管线串行执行：OCR 缓存 → 结构化抽取 → 入库 → 公司级同比重算 → 主数据回写。
            {{ ocrCapability }}。研报支持纯文本直读。
          </p>
          <div class="admin__upload-row">
            <div class="admin__filetype">
              <label :class="{ active: fileType === 'financial' }">
                <input v-model="fileType" type="radio" value="financial" />财报（入库+指标）
              </label>
              <label :class="{ active: fileType === 'research' }">
                <input v-model="fileType" type="radio" value="research" />研报（RAG 知识库）
              </label>
            </div>
            <AppButton :loading="uploading" @click="fileInput?.click()">
              {{ uploading ? "上传中…" : "选择 PDF 上传" }}
            </AppButton>
            <input ref="fileInput" type="file" accept=".pdf" hidden @change="onUpload" />
            <span v-if="uploadMessage" class="admin__upload-msg">{{ uploadMessage }}</span>
          </div>
        </AppCard>

        <AppCard padding="lg">
          <template #header>
            <div class="panel__title-row">
              <AppSection
                title="任务列表"
                :description="`排队 ${taskStats.pending ?? 0} · 执行 ${taskStats.running ?? 0} · 成功 ${taskStats.success ?? 0} · 部分完成 ${taskStats.partial ?? 0} · 失败 ${taskStats.failed ?? 0}`"
                dense
              />
              <AppButton variant="outline" size="sm" @click="loadTasks">刷新</AppButton>
            </div>
          </template>
          <div class="admin__table-wrap">
            <table class="admin__table">
              <thead>
                <tr><th>#</th><th>文件</th><th>类型</th><th>状态</th><th>结果</th><th>时间</th></tr>
              </thead>
              <tbody>
                <tr v-for="task in tasks" :key="task.id">
                  <td class="mono">{{ task.id }}</td>
                  <td class="admin__file" :title="task.fileName">{{ task.fileName }}</td>
                  <td>{{ task.fileType === "financial" ? "财报" : "研报" }}</td>
                  <td>
                    <AppBadge :variant="badgeVariant(task.status)">
                      {{ statusLabels[task.status] ?? task.status }}
                    </AppBadge>
                  </td>
                  <td class="admin__msg">
                    <div :title="task.message">{{ task.message || "—" }}</div>
                    <div v-if="stepSummary(task)" class="admin__steps mono">{{ stepSummary(task) }}</div>
                    <AppButton v-if="task.status === 'FAILED' || task.status === 'PARTIAL'" size="sm" variant="outline" :disabled="retryingTask !== null" :loading="retryingTask === task.id" @click="retryTask(task)">重试处理</AppButton>
                  </td>
                  <td class="mono admin__time">{{ task.createdAt?.replace("T", " ").slice(0, 16) }}</td>
                </tr>
                <tr v-if="!tasks.length">
                  <td colspan="6">
                    <AppEmpty title="暂无任务" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="taskTotal > 10" class="admin__pager">
            <AppButton variant="outline" size="sm" :disabled="taskPage <= 1" @click="taskPage -= 1">上一页</AppButton>
            <span class="mono">{{ taskPage }}</span>
            <AppButton variant="outline" size="sm" :disabled="taskPage * 10 >= taskTotal" @click="taskPage += 1">下一页</AppButton>
          </div>
        </AppCard>
      </section>

      <!-- 作品说明：用户管理 -->
      <section v-else-if="tab === 'users'" class="admin__section">
        <AppCard padding="lg">
          <template #header>
            <div class="panel__title-row">
              <AppSection title="用户列表" :description="`共 ${userTotal} 人`" dense />
              <input
                v-model="userKeyword"
                class="admin__search"
                placeholder="搜索用户名/昵称…"
                @keydown.enter="userPage = 1; loadUsers()"
              />
            </div>
          </template>
          <div class="admin__table-wrap">
            <table class="admin__table">
              <thead>
                <tr><th>#</th><th>用户名</th><th>昵称</th><th>角色</th><th>风险偏好</th><th>状态</th><th>注册时间</th><th></th></tr>
              </thead>
              <tbody>
                <tr v-for="user in users" :key="user.id">
                  <td class="mono">{{ user.id }}</td>
                  <td>{{ user.username }}</td>
                  <td>{{ user.nickname || "—" }}</td>
                  <td>
                    <AppBadge :variant="user.role === 'ADMIN' ? 'primary' : 'default'">{{ user.role }}</AppBadge>
                  </td>
                  <td>{{ user.riskProfile }}</td>
                  <td>
                    <AppBadge :variant="user.status === 1 ? 'success' : 'danger'">
                      {{ user.status === 1 ? "正常" : "禁用" }}
                    </AppBadge>
                  </td>
                  <td class="mono admin__time">{{ user.createdAt?.replace("T", " ").slice(0, 16) }}</td>
                  <td>
                    <button v-if="user.role !== 'ADMIN'" type="button" class="link-btn" @click="toggleUser(user)">
                      {{ user.status === 1 ? "禁用" : "启用" }}
                    </button>
                  </td>
                </tr>
                <tr v-if="!users.length">
                  <td colspan="8"><AppEmpty title="暂无用户" /></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="userTotal > 10" class="admin__pager">
            <AppButton variant="outline" size="sm" :disabled="userPage <= 1" @click="userPage -= 1">上一页</AppButton>
            <span class="mono">{{ userPage }}</span>
            <AppButton variant="outline" size="sm" :disabled="userPage * 10 >= userTotal" @click="userPage += 1">下一页</AppButton>
          </div>
        </AppCard>
      </section>

      <!-- 作品说明：对话审计 -->
      <section v-else class="admin__section">
        <AppCard padding="lg">
          <template #header>
            <AppSection title="对话审计" :description="`MQ 异步落库，共 ${logTotal} 条`" dense />
          </template>
          <div class="admin__table-wrap">
            <table class="admin__table">
              <thead>
                <tr><th>#</th><th>用户</th><th>问题</th><th>状态</th><th>耗时</th><th>traceId</th><th>时间</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in logs" :key="item.id">
                  <td class="mono">{{ item.id }}</td>
                  <td class="mono">{{ item.userId }}</td>
                  <td class="admin__question" :title="item.question">{{ item.question }}</td>
                  <td>
                    <AppBadge :variant="badgeVariant(item.status)">{{ item.status }}</AppBadge>
                  </td>
                  <td class="mono">{{ (item.durationMs / 1000).toFixed(1) }}s</td>
                  <td class="mono admin__trace" :title="item.requestId">{{ item.requestId.slice(0, 8) }}</td>
                  <td class="mono admin__time">{{ item.createdAt?.replace("T", " ").slice(0, 16) }}</td>
                </tr>
                <tr v-if="!logs.length">
                  <td colspan="7"><AppEmpty title="暂无审计日志" /></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="logTotal > 12" class="admin__pager">
            <AppButton variant="outline" size="sm" :disabled="logPage <= 1" @click="logPage -= 1">上一页</AppButton>
            <span class="mono">{{ logPage }}</span>
            <AppButton variant="outline" size="sm" :disabled="logPage * 12 >= logTotal" @click="logPage += 1">下一页</AppButton>
          </div>
        </AppCard>
      </section>
    </main>
  </div>
</template>

<style scoped>
.admin {
  min-height: 100%;
  background: var(--c-bg);
}

.admin__main {
  max-width: var(--page-max-w);
  margin: 0 auto;
  padding: var(--sp-6) var(--page-pad-x) var(--sp-12);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.admin__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-5);
}

.admin__eyebrow,
.overview__kicker,
.overview-card__eyebrow {
  display: block;
  color: var(--c-primary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.11em;
}

.admin__header h1 {
  margin-top: 5px;
  font-size: var(--fs-2xl);
  font-weight: 800;
  color: var(--c-text);
  letter-spacing: -0.02em;
}

.admin__header p {
  margin-top: 5px;
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
}

.admin__identity {
  flex: 0 0 auto;
  padding: 7px 12px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface);
  color: var(--c-text-secondary);
  font-size: var(--fs-xs);
}

.admin__tabs {
  width: fit-content;
  max-width: 100%;
  display: flex;
  gap: 3px;
  padding: 4px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  background: var(--c-surface-muted);
  overflow-x: auto;
  scrollbar-width: none;
}

.admin__tabs::-webkit-scrollbar {
  display: none;
}

.admin__tab {
  flex: 0 0 auto;
  border: none;
  background: transparent;
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
  font-weight: 500;
  padding: 7px var(--sp-4);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: background var(--t-fast), color var(--t-fast), box-shadow var(--t-fast);
}

.admin__tab:hover {
  color: var(--c-text);
  background: color-mix(in srgb, var(--c-surface) 68%, transparent);
}

.admin__tab--active {
  background: var(--c-surface);
  color: var(--c-primary);
  box-shadow: 0 1px 3px rgb(15 23 42 / 10%);
}

.admin__section {
  display: flex;
  flex-direction: column;
  gap: var(--section-gap);
}

.admin__section--overview {
  gap: var(--sp-5);
}

.overview__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: var(--sp-5) var(--sp-6);
  border: 1px solid var(--c-primary-border);
  border-radius: var(--r-lg);
  background:
    radial-gradient(circle at 88% 18%, rgb(55 111 222 / 13%) 0, transparent 30%),
    linear-gradient(135deg, var(--c-surface) 0%, var(--c-primary-soft) 100%);
}

.overview__head h2 {
  margin-top: 5px;
  color: var(--c-text);
  font-size: var(--fs-xl);
  line-height: 1.25;
}

.overview__head p {
  margin-top: 5px;
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
}

.overview__error {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid color-mix(in srgb, var(--c-danger) 26%, transparent);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}

.overview__error span {
  flex: 1;
}

.overview__error button {
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.overview__loading-grid,
.overview__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-3);
}

.overview__loading-card,
.metric-card {
  min-width: 0;
  padding: var(--sp-4) var(--sp-5);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  background: var(--c-surface);
  box-shadow: 0 1px 2px rgb(15 23 42 / 3%);
}

.overview__loading-card {
  min-height: 122px;
  display: flex;
  align-items: center;
}

.metric-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  transition: border-color var(--t-fast), box-shadow var(--t-fast), transform var(--t-fast);
}

.metric-card:hover {
  border-color: var(--c-primary-border);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.metric-card__topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
}

.metric-card__label,
.metric-card__helper {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.metric-card__icon {
  width: 32px;
  height: 32px;
  border-radius: var(--r-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.metric-card__icon--primary {
  color: var(--c-primary);
  background: var(--c-primary-soft);
}

.metric-card__icon--violet {
  color: #7653c7;
  background: #f0ebff;
}

.metric-card__icon--teal {
  color: #087f75;
  background: #e6f7f4;
}

.metric-card__icon--orange {
  color: #b65f13;
  background: #fff1e4;
}

.metric-card__value {
  color: var(--c-text);
  font-size: clamp(26px, 2.2vw, 34px);
  line-height: 1.1;
  letter-spacing: -0.035em;
}

.overview__panels {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
  gap: var(--sp-4);
  align-items: stretch;
}

.overview-card {
  min-width: 0;
  padding: var(--sp-5);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  background: var(--c-surface);
  box-shadow: 0 1px 2px rgb(15 23 42 / 3%);
}

.overview-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
}

.overview-card__head h3 {
  margin-top: 4px;
  color: var(--c-text);
  font-size: var(--fs-lg);
}

.overview-card__head p {
  margin-top: 3px;
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
}

.overview-card__head > svg {
  color: var(--c-primary);
}

.overview-card__action {
  flex: 0 0 auto;
  border: none;
  background: transparent;
  color: var(--c-primary);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
}

.pipeline__summary {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-top: var(--sp-6);
}

.pipeline__summary > div {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
}

.pipeline__summary strong {
  color: var(--c-text);
  font-size: 36px;
  line-height: 1;
  letter-spacing: -0.04em;
}

.pipeline__summary span,
.pipeline__summary p {
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
}

.pipeline__progress {
  height: 8px;
  margin-top: var(--sp-3);
  overflow: hidden;
  border-radius: var(--r-full);
  background: var(--c-surface-muted);
}

.pipeline__progress span {
  display: block;
  height: 100%;
  min-width: 0;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--c-primary), #58a6ff);
  transition: width var(--t-base);
}

.pipeline__breakdown {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--sp-2);
  margin-top: var(--sp-5);
}

.pipeline__status {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  column-gap: 7px;
  row-gap: 3px;
  padding: var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-surface-muted);
  color: var(--c-text-secondary);
  font-size: var(--fs-xs);
}

.pipeline__status strong {
  grid-column: 2;
  color: var(--c-text);
  font-size: var(--fs-md);
}

.pipeline__dot {
  grid-row: 1 / span 2;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--c-text-tertiary);
}

.pipeline__dot--success {
  background: var(--c-success);
}

.pipeline__dot--primary {
  background: var(--c-primary);
}

.pipeline__dot--warning {
  background: var(--c-warning);
}

.pipeline__dot--danger {
  background: var(--c-danger);
}

.hot-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: var(--sp-4);
}

.hot-list__item {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--sp-2);
  min-height: 48px;
  padding: 6px var(--sp-2);
  border-radius: var(--r-md);
  color: var(--c-text);
  transition: background var(--t-fast);
}

.hot-list__item:hover {
  background: var(--c-primary-soft);
  text-decoration: none;
}

.hot-list__rank {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--c-surface-muted);
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
  font-weight: 700;
}

.hot-list__rank--top {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.hot-list__company {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.hot-list__company strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-sm);
}

.hot-list__company small,
.hot-list__views {
  color: var(--c-text-tertiary);
  font-size: 11px;
}

.hot-list__empty {
  min-height: 215px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--c-text-secondary);
}

.hot-list__empty-icon {
  width: 46px;
  height: 46px;
  margin-bottom: var(--sp-3);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-lg);
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.hot-list__empty strong {
  color: var(--c-text);
  font-size: var(--fs-sm);
}

.hot-list__empty p {
  max-width: 240px;
  margin-top: 5px;
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
  line-height: 1.55;
}

.admin__hint {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  margin-bottom: var(--sp-3);
  line-height: 1.6;
}

.admin__upload-row {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  flex-wrap: wrap;
}

.admin__filetype {
  display: flex;
  gap: var(--sp-2);
}

.admin__filetype label {
  font-size: var(--fs-sm);
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 6px var(--sp-3);
  cursor: pointer;
  color: var(--c-text-secondary);
  transition: all var(--t-fast);
}

.admin__filetype label.active {
  border-color: var(--c-primary);
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.admin__filetype input {
  display: none;
}

.admin__upload-msg {
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.admin__table-wrap {
  overflow-x: auto;
}

.admin__table {
  width: 100%;
  min-width: 860px;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.admin__table th {
  text-align: left;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  font-weight: 600;
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}

.admin__table td {
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border);
  vertical-align: top;
}

.admin__table tr:last-child td {
  border-bottom: none;
}

.admin__file,
.admin__question {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin__msg {
  max-width: 280px;
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
}

.admin__msg > div:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin__steps {
  margin-top: 2px;
  color: var(--c-text-tertiary);
  font-size: 11px;
}

.admin__time {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  white-space: nowrap;
}

.admin__trace {
  font-size: var(--fs-xs);
}

.admin__search {
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 6px var(--sp-3);
  font-size: var(--fs-sm);
  background: var(--c-bg);
  color: var(--c-text);
  width: 200px;
}

.admin__search:focus {
  outline: none;
  border-color: var(--c-primary);
}

.admin__pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
  margin-top: var(--sp-3);
}

.link-btn {
  border: none;
  background: none;
  color: var(--c-primary);
  font-size: var(--fs-xs);
  cursor: pointer;
  padding: 0;
}

.mono {
  font-family: var(--font-mono);
}

@media (max-width: 1100px) {
  .overview__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview__panels {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .admin__main {
    padding: var(--sp-4) var(--sp-3) var(--sp-8);
  }

  .overview__loading-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pipeline__breakdown {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .admin__upload-row,
  .admin__filetype {
    align-items: stretch;
    flex-direction: column;
  }

  .admin__search {
    width: 100%;
  }
}

@media (max-width: 640px) {
  .admin__header,
  .overview__head {
    align-items: stretch;
    flex-direction: column;
  }

  .admin__identity {
    align-self: flex-start;
  }

  .admin__tabs {
    width: 100%;
  }

  .overview__head {
    padding: var(--sp-4);
  }

  .overview__head :deep(.app-btn) {
    width: 100%;
  }

  .overview__metrics,
  .overview__loading-grid {
    grid-template-columns: 1fr;
  }

  .overview-card {
    padding: var(--sp-4);
  }

  .pipeline__summary {
    align-items: flex-start;
    flex-direction: column;
  }

  .pipeline__breakdown {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hot-list__views {
    display: none;
  }
}
</style>
