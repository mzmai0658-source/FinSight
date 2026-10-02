<script setup lang="ts">
import { advisorEnabled } from "@/features";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import AppButton from "@/components/ui/AppButton.vue";
import AppCard from "@/components/ui/AppCard.vue";
import AppEmpty from "@/components/ui/AppEmpty.vue";
import AppSection from "@/components/ui/AppSection.vue";
import PageHeader from "@/components/layout/PageHeader.vue";
import PortalShell from "@/components/layout/PortalShell.vue";
import { fmtPct, fmtYi } from "@/components/market/chartTheme";
import {
  fetchNotifications,
  fetchWatchlist,
  markAllNotificationsRead,
  markNotificationRead,
  removeWatch,
  updateProfile,
} from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { NotificationItem, WatchlistItem } from "@/types/platform";

const auth = useAuthStore();
const router = useRouter();

/* 作品说明：资料编辑 */

const nickname = ref(auth.user?.nickname ?? "");
const riskProfile = ref(auth.user?.riskProfile ?? "balanced");
const saving = ref(false);
const saveMessage = ref("");

const riskOptions = [
  { value: "conservative", label: "保守型", desc: "注重防御与确定性" },
  { value: "balanced", label: "稳健型", desc: "平衡收益与风险" },
  { value: "aggressive", label: "进取型", desc: "接受高波动换取弹性" },
];

async function saveProfile() {
  saving.value = true;
  saveMessage.value = "";
  try {
    const user = await updateProfile({ nickname: nickname.value, riskProfile: riskProfile.value });
    auth.user = user;
    saveMessage.value = "已保存";
    window.setTimeout(() => (saveMessage.value = ""), 2000);
  } catch (e) {
    saveMessage.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}

/* 作品说明：自选股 */

const watchlist = ref<WatchlistItem[]>([]);
const watchlistLoading = ref(true);
const watchlistError = ref("");
const removingCode = ref("");

async function loadWatchlist() {
  watchlistLoading.value = true;
  watchlistError.value = "";
  try {
    watchlist.value = await fetchWatchlist();
  } catch {
    watchlistError.value = "关注列表加载失败，请重试。";
  } finally {
    watchlistLoading.value = false;
  }
}

async function unwatch(code: string) {
  if (removingCode.value) return;
  removingCode.value = code;
  watchlistError.value = "";
  try {
    await removeWatch(code);
    watchlist.value = watchlist.value.filter((item) => item.stockCode !== code);
  } catch {
    watchlistError.value = "取消关注失败，请稍后重试。";
  } finally {
    removingCode.value = "";
  }
}

/* 作品说明：站内信 */

const notifications = ref<NotificationItem[]>([]);
const notifyTotal = ref(0);
const notifyPage = ref(1);
const notificationsLoading = ref(true);
const notificationsError = ref("");
const markingRead = ref(false);

async function loadNotifications() {
  notificationsLoading.value = true;
  notificationsError.value = "";
  try {
    const result = await fetchNotifications(notifyPage.value, 8);
    notifications.value = result.records;
    notifyTotal.value = result.total;
  } catch {
    notificationsError.value = "通知加载失败，请重试。";
  } finally {
    notificationsLoading.value = false;
  }
}

async function openNotification(item: NotificationItem) {
  notificationsError.value = "";
  try {
    if (!item.readFlag) {
      await markNotificationRead(item.id);
      item.readFlag = 1;
    }
    if (item.link) await router.push(item.link);
  } catch {
    notificationsError.value = "通知暂时无法打开，请重试。";
  }
}

async function readAll() {
  markingRead.value = true;
  notificationsError.value = "";
  try {
    await markAllNotificationsRead();
    notifications.value.forEach((item) => (item.readFlag = 1));
  } catch {
    notificationsError.value = "标记已读失败，请重试。";
  } finally {
    markingRead.value = false;
  }
}

function turnNotifications(delta: number) {
  notifyPage.value += delta;
  void loadNotifications();
}

onMounted(() => {
  void loadWatchlist();
  void loadNotifications();
});

async function onLogout() {
  await auth.logout();
  router.push({ name: "home" });
}
</script>

<template>
  <PortalShell>
    <PageHeader title="个人中心" :description="`${auth.user?.nickname || auth.user?.username || ''}，在这里管理资料、关注的公司和站内通知。`">
      <template #actions>
        <AppButton variant="outline" size="sm" @click="onLogout">退出登录</AppButton>
      </template>
    </PageHeader>

      <div class="profile__grid">
        <!-- 作品说明：资料 + 风险偏好 -->
        <AppCard padding="lg">
          <template #header>
            <AppSection title="基本资料" dense />
          </template>
          <div class="profile__field">
            <label>用户名</label>
            <span class="mono">{{ auth.user?.username }}</span>
          </div>
          <div class="profile__field">
            <label for="profile-nickname">昵称</label>
            <input id="profile-nickname" v-model="nickname" maxlength="32" placeholder="设置昵称" />
          </div>
          <div v-if="advisorEnabled" class="profile__field profile__field--col">
            <label>阅读偏好（影响财务画像的展示侧重）</label>
            <div class="profile__risks">
              <button
                v-for="opt in riskOptions"
                :key="opt.value"
                type="button"
                class="profile__risk"
                :class="{ 'profile__risk--active': riskProfile === opt.value }"
                @click="riskProfile = opt.value"
              >
                <strong>{{ opt.label }}</strong>
                <span>{{ opt.desc }}</span>
              </button>
            </div>
          </div>
          <div class="profile__actions">
            <AppButton :loading="saving" @click="saveProfile">
              {{ saving ? "保存中…" : "保存" }}
            </AppButton>
            <span v-if="saveMessage" class="profile__save-msg" role="status">{{ saveMessage }}</span>
          </div>
        </AppCard>

        <!-- 作品说明：站内信 -->
        <AppCard padding="lg">
          <template #header>
            <div class="panel__title-row">
              <AppSection title="站内信" dense />
              <AppButton
                v-if="notifications.some((n) => !n.readFlag)"
                variant="ghost"
                size="sm"
                :loading="markingRead"
                @click="readAll"
              >
                全部已读
              </AppButton>
            </div>
          </template>
          <div v-if="notificationsError" class="profile__load-error" role="alert">{{ notificationsError }}<AppButton variant="ghost" size="sm" @click="loadNotifications">重新加载</AppButton></div>
          <p v-if="notificationsLoading" class="profile__loading" role="status">正在加载通知…</p>
          <AppEmpty v-else-if="!notifications.length && !notificationsError" title="暂无通知" />
          <ul v-else-if="notifications.length" class="notify__list">
            <li
              v-for="item in notifications"
              :key="item.id"
              class="notify__item"
              :class="{ 'notify__item--unread': !item.readFlag }"
              role="button"
              tabindex="0"
              @click="openNotification(item)"
              @keydown.enter="openNotification(item)"
              @keydown.space.prevent="openNotification(item)"
            >
              <div class="notify__row">
                <span class="notify__title">{{ item.title }}</span>
                <span class="notify__date mono">{{ item.createdAt?.replace("T", " ").slice(0, 16) }}</span>
              </div>
              <p class="notify__content">{{ item.content }}</p>
            </li>
          </ul>
          <nav v-if="notifyTotal > 8" class="profile__pager" aria-label="通知分页">
            <AppButton variant="outline" size="sm" :disabled="notifyPage <= 1 || notificationsLoading" @click="turnNotifications(-1)">上一页</AppButton>
            <span>{{ notifyPage }} / {{ Math.ceil(notifyTotal / 8) }}</span>
            <AppButton variant="outline" size="sm" :disabled="notifyPage * 8 >= notifyTotal || notificationsLoading" @click="turnNotifications(1)">下一页</AppButton>
          </nav>
        </AppCard>
      </div>

      <!-- 作品说明：自选股 -->
      <AppCard padding="lg">
        <template #header>
          <AppSection title="关注的公司" :description="`${watchlist.length} 家 · 指标取最新已入库年报`" dense />
        </template>
        <div v-if="watchlistError" class="profile__load-error" role="alert">{{ watchlistError }}<AppButton variant="ghost" size="sm" @click="loadWatchlist">重新加载</AppButton></div>
        <p v-if="watchlistLoading" class="profile__loading" role="status">正在加载关注的公司…</p>
        <AppEmpty v-else-if="!watchlist.length && !watchlistError" title="还没有关注的公司">
          去
          <router-link to="/market">公司数据</router-link>
          关注感兴趣的公司
        </AppEmpty>
        <div v-else-if="watchlist.length" class="watch__table-wrap">
          <table class="watch__table">
            <thead>
              <tr>
                <th>公司</th>
                <th>行业</th>
                <th class="num">营收（最新年报）</th>
                <th class="num">净利润</th>
                <th class="num">净利同比</th>
                <th class="num">ROE</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in watchlist" :key="item.stockCode">
                <td>
                  <router-link :to="`/market/${item.stockCode}`" class="watch__name">
                    {{ item.abbr ?? item.stockCode }}
                    <span class="mono watch__code">{{ item.stockCode }}</span>
                  </router-link>
                </td>
                <td class="watch__industry">{{ (item.industry ?? "").replace(/^.*-/, "") || "—" }}</td>
                <td class="num">{{ item.revenue == null ? "—" : fmtYi(item.revenue) }}</td>
                <td class="num">{{ item.netProfit == null ? "—" : fmtYi(item.netProfit) }}</td>
                <td class="num" :class="item.netProfitYoy == null ? '' : item.netProfitYoy >= 0 ? 'up' : 'down'">
                  {{ item.netProfitYoy == null ? "—" : fmtPct(item.netProfitYoy) }}
                </td>
                <td class="num">{{ item.roe == null ? "—" : fmtPct(item.roe) }}</td>
                <td class="num">
                  <button type="button" class="link-btn" :disabled="!!removingCode" @click="unwatch(item.stockCode)">{{ removingCode === item.stockCode ? '处理中…' : '取消关注' }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </AppCard>
  </PortalShell>
</template>

<style scoped>
.profile__load-error { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2); color: var(--c-danger); font-size: var(--fs-sm); padding: var(--sp-3) 0; }
.profile__loading { color: var(--c-text-secondary); padding: var(--sp-5) 0; }
.profile__pager { display: flex; justify-content: center; align-items: center; gap: var(--sp-3); padding-top: var(--sp-4); font-size: var(--fs-sm); }
.profile__grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
  gap: var(--section-gap);
}

.panel__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
  width: 100%;
}

.profile__field {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-bottom: var(--sp-3);
  font-size: var(--fs-sm);
}

.profile__field--col {
  flex-direction: column;
  align-items: stretch;
}

.profile__field label {
  color: var(--c-text-tertiary);
  min-width: 56px;
  font-size: var(--fs-xs);
  font-weight: 500;
}

.profile__field input {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  padding: 7px var(--sp-3);
  font-size: var(--fs-sm);
  background: var(--c-bg);
  color: var(--c-text);
}

.profile__field input:focus {
  outline: none;
  border-color: var(--c-primary);
}

.profile__risks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: var(--sp-2);
}

.profile__risk {
  border: 1px solid var(--c-border);
  background: var(--c-bg);
  border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: 2px;
  cursor: pointer;
  text-align: left;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  transition: border-color var(--t-fast), background var(--t-fast);
}

.profile__risk strong {
  font-size: var(--fs-sm);
  color: var(--c-text);
}

.profile__risk--active {
  border-color: var(--c-primary);
  background: var(--c-primary-soft);
}

.profile__risk--active strong {
  color: var(--c-primary);
}

.profile__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-top: var(--sp-2);
}

.profile__save-msg {
  font-size: var(--fs-sm);
  color: var(--c-success);
}

/* 作品说明：站内信 */
.notify__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.notify__item {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3);
  cursor: pointer;
  transition: border-color var(--t-fast), background var(--t-fast);
}

.notify__item:hover {
  border-color: var(--c-primary-border);
}

.notify__item--unread {
  border-color: var(--c-primary-border);
  background: var(--c-primary-soft);
}

.notify__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.notify__title {
  font-size: var(--fs-sm);
  font-weight: 600;
}

.notify__date {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.notify__content {
  margin-top: 2px;
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
}

/* 作品说明：自选表格 */
.watch__table-wrap {
  overflow-x: auto;
}

.watch__table {
  width: 100%;
  min-width: 860px;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.watch__table th {
  text-align: left;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  font-weight: 600;
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}

.watch__table td {
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}

.watch__table tr:last-child td {
  border-bottom: none;
}

.watch__table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.watch__name {
  font-weight: 600;
  color: var(--c-text);
}

.watch__name:hover {
  color: var(--c-primary);
  text-decoration: none;
}

.watch__code {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  margin-left: 4px;
}

.watch__industry {
  color: var(--c-text-secondary);
}

.up {
  color: var(--c-danger);
}

.down {
  color: var(--c-success);
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

@media (max-width: 900px) {
  .profile__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
