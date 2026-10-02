<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppButton from "@/components/ui/AppButton.vue";
import ChatInput from "@/components/chat/ChatInput.vue";
import ChatMessage from "@/components/chat/ChatMessage.vue";
import ContextChips from "@/components/chat/ContextChips.vue";
import { viewFromMessage, viewFromStreaming, type MessageView } from "@/components/chat/messageView";
import EvidenceRail from "@/components/EvidenceRail.vue";
import ResponsivePanel from "@/components/layout/ResponsivePanel.vue";
import { useMediaQuery } from "@/composables/useMediaQuery";
import StatusDots from "@/components/StatusDots.vue";
import {
  IconChart,
  IconChevronRight,
  IconClose,
  IconDatabase,
  IconDocument,
  IconList,
  IconMenu,
  IconPanelRight,
  IconPlus,
  IconSpark,
} from "@/components/ui/icons";
import { fetchHealth } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import { useExamplesStore } from "@/stores/examples";
import { useSessionStore } from "@/stores/session";
import { useUiStore, type EvidenceTab } from "@/stores/ui";

const route = useRoute();
const router = useRouter();
const sessionStore = useSessionStore();
const examplesStore = useExamplesStore();
const auth = useAuthStore();
const ui = useUiStore();
const compact = useMediaQuery("(max-width: 960px)");
const sidebarOpen = ref(false);
const bootstrapping = ref(true);
const changingSession = ref(false);
const sessionSearch = ref("");
const navigating = computed(() => bootstrapping.value || changingSession.value || sessionStore.loadingMessages);
const busy = computed(() => navigating.value || sessionStore.isStreaming);
const visibleSessions = computed(() => sessionStore.sessions.filter((item) => item.title.toLowerCase().includes(sessionSearch.value.trim().toLowerCase())));

watch(compact, () => { sidebarOpen.value = false; });

const scrollEl = ref<HTMLDivElement | null>(null);
const autoFollow = ref(true);

const messageViews = computed<MessageView[]>(() =>
  sessionStore.messages.map((message, index) => viewFromMessage(message, index)),
);

const streamingViews = computed(() =>
  sessionStore.streaming.active ? viewFromStreaming(sessionStore.streaming) : null,
);

const hasConversation = computed(
  () => messageViews.value.length > 0 || sessionStore.streaming.active,
);

const primaryLinks = computed(() => [
  { to: "/market", label: "公司数据", icon: IconChart },
  { to: "/reports", label: "资料库", icon: IconDocument },
  { to: "/profile", label: "个人中心", icon: IconList },
  ...(auth.user?.role === "ADMIN" ? [{ to: "/admin", label: "管理端", icon: IconDatabase }] : []),
]);

const activeTitle = computed(
  () => sessionStore.sessions.find((item) => item.sessionUid === sessionStore.activeUid)?.title || "新对话",
);

/** 作品说明：证据栏聚焦的消息视图：流式优先，其次用户选中的，最后取最新一条助手消息 */
const focusedView = computed<MessageView | null>(() => {
  if (streamingViews.value) return streamingViews.value.assistant;
  if (ui.focusedMessageIndex >= 0 && ui.focusedMessageIndex < messageViews.value.length) {
    const view = messageViews.value[ui.focusedMessageIndex];
    if (view?.role === "assistant") return view;
  }
  for (let i = messageViews.value.length - 1; i >= 0; i -= 1) {
    if (messageViews.value[i].role === "assistant") return messageViews.value[i];
  }
  return null;
});

onMounted(async () => {
  examplesStore.ensureLoaded();
  fetchHealth()
    .then((health) => ui.setHealth(health))
    .catch(() => ui.setHealth(null));

  try {
    await sessionStore.bootstrap();
    const presetQuestion = String(route.query.q ?? "").trim();
    if (presetQuestion) {
      await sessionStore.newSession();
      await router.replace({ query: { ...route.query, q: undefined } });
      bootstrapping.value = false;
      void send(presetQuestion);
    }
  } catch {
    ui.setError(route.query.q ? "会话加载失败，请刷新页面重试。你的问题仍保留在当前链接中。" : "会话加载失败，请刷新页面重试。");
  } finally {
    bootstrapping.value = false;
  }
  scrollToBottom(true);
});

function onScroll() {
  const el = scrollEl.value;
  if (!el) return;
  autoFollow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
}

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = scrollEl.value;
    if (!el) return;
    if (force || autoFollow.value) {
      el.scrollTop = el.scrollHeight;
    }
  });
}

watch(
  () => [
    sessionStore.streaming.content,
    sessionStore.streaming.steps.length,
    sessionStore.messages.length,
  ],
  () => scrollToBottom(),
);

async function send(question: string) {
  if (busy.value) return;
  sidebarOpen.value = false;
  ui.setError("");
  ui.focusMessage(-1);
  autoFollow.value = true;
  scrollToBottom(true);
  try {
    await sessionStore.sendQuestion(question);
  } catch (error) {
    ui.setError(error instanceof Error ? error.message : "请求失败，请检查后端服务是否启动。");
  }
}

function stop() {
  sessionStore.stopStreaming();
}

async function newConversation() {
  if (navigating.value) return;
  changingSession.value = true;
  ui.setError("");
  ui.focusMessage(-1);
  try {
    await sessionStore.newSession();
    sidebarOpen.value = false;
  } catch {
    ui.setError("新建会话失败，请稍后重试。");
  } finally {
    changingSession.value = false;
  }
}

async function openSession(uid: string) {
  if (navigating.value) return;
  if (uid === sessionStore.activeUid) { sidebarOpen.value = false; return; }
  ui.setError("");
  ui.focusMessage(-1);
  try {
    await sessionStore.openSession(uid);
    sidebarOpen.value = false;
    scrollToBottom(true);
  } catch {
    ui.setError("打开会话失败，请重试。");
  }
}

async function removeSession(uid: string, event: Event) {
  event.stopPropagation();
  if (busy.value) return;
  changingSession.value = true;
  try {
    await sessionStore.removeSession(uid);
    ui.focusMessage(-1);
  } catch {
    ui.setError("删除会话失败，请重试。");
  } finally {
    changingSession.value = false;
  }
}

async function onLogout() {
  await auth.logout();
}

function openDetail(tab: EvidenceTab, messageIndex: number) {
  ui.openRail(tab, messageIndex);
}

function retryMessage(messageIndex: number) {
  void sessionStore.retryMessage(messageIndex).catch((error) => { ui.setError(error instanceof Error ? error.message : "重试失败。"); });
}
</script>

<template>
  <div class="ws">
    <header class="ws__topbar">
      <button type="button" class="ws__bar-btn" aria-label="打开会话与导航" :aria-expanded="sidebarOpen" @click="sidebarOpen = true">
        <IconMenu :size="18" />
      </button>
      <router-link class="ws__topbar-brand" to="/">
        <span class="ws__brand-mark"><IconSpark :size="15" /></span>
        FinSight
      </router-link>
      <button
        type="button"
        class="ws__topbar-new"
        :disabled="navigating"
        title="新对话"
        aria-label="新对话"
        @click="newConversation"
      >
        <IconPlus :size="15" />
      </button>
    </header>

    <!-- 作品说明：左侧：会话为主体 -->
    <ResponsivePanel :overlay="compact" :open="sidebarOpen" side="left" label="会话与导航" @close="sidebarOpen = false">
      <aside class="ws__left" aria-label="会话与导航">
      <div class="ws__sidebar-head">
      <router-link class="ws__brand" to="/">
        <span class="ws__brand-mark"><IconSpark :size="15" /></span>
        <span class="ws__brand-text">
          <strong>FinSight</strong>
          <small>AI 工作台</small>
        </span>
      </router-link>
        <button v-if="compact" type="button" class="ws__bar-btn" aria-label="关闭会话与导航" @click="sidebarOpen = false"><IconClose :size="16" /></button>
      </div>

      <AppButton block :disabled="navigating" @click="newConversation">
        <IconPlus :size="15" />
        新对话
      </AppButton>

      <div class="ws__section ws__section--sessions">
        <h3 class="ws__section-title">会话</h3>
        <input v-if="sessionStore.sessions.length > 5" v-model="sessionSearch" class="ws__session-search" type="search" placeholder="搜索历史会话" aria-label="搜索历史会话" />
        <div class="ws__sessions">
          <div
            v-for="item in visibleSessions"
            :key="item.sessionUid"
            class="ws__session"
            :class="{ 'ws__session--active': item.sessionUid === sessionStore.activeUid }"
          >
            <button type="button" class="ws__session-title" :title="item.title" :aria-current="item.sessionUid === sessionStore.activeUid ? 'true' : undefined" :disabled="navigating" @click="openSession(item.sessionUid)">{{ item.title }}</button>
            <button
              class="ws__session-del"
              type="button"
              title="删除会话"
              :aria-label="`删除会话：${item.title}`"
              :disabled="busy"
              @click="removeSession(item.sessionUid, $event)"
            >
              <IconClose :size="12" />
            </button>
          </div>
          <p v-if="sessionStore.loadingSessions" class="ws__sessions-empty" role="status">正在加载会话…</p>
          <p v-else-if="!sessionStore.sessions.length" class="ws__sessions-empty">还没有会话，发一条消息开始吧</p>
          <p v-else-if="!visibleSessions.length" class="ws__sessions-empty">没有匹配的会话，试试其他关键词</p>
        </div>
      </div>

      <details v-if="examplesStore.examples.length" class="ws__examples" :open="!hasConversation">
        <summary class="ws__section-title">试试这样问</summary>
        <button
          v-for="example in examplesStore.examples.slice(0, 4)"
          :key="example.id"
          class="ws__example"
          type="button"
          :disabled="busy"
          @click="send(example.question)"
        >
          {{ example.question }}
        </button>
      </details>

      <nav class="ws__links" aria-label="其他页面">
        <router-link v-for="link in primaryLinks" :key="link.to" class="ws__link" :to="link.to">
          <component :is="link.icon" :size="14" />
          {{ link.label }}
        </router-link>
      </nav>

      <footer class="ws__left-footer">
        <StatusDots :health="ui.health" />
        <div class="ws__user">
          <span class="ws__user-name">{{ auth.user?.nickname || auth.user?.username }}</span>
          <button class="ws__user-logout" type="button" @click="onLogout">退出</button>
        </div>
      </footer>
      </aside>
    </ResponsivePanel>

    <!-- 作品说明：中央对话区 -->
    <main class="ws__center">
      <header class="ws__bar">
        <h1 class="ws__bar-title" :title="activeTitle">{{ hasConversation ? activeTitle : "新对话" }}</h1>
        <button
          type="button"
          class="ws__bar-btn"
          :class="{ 'ws__bar-btn--active': ui.railExpanded }"
          :aria-pressed="ui.railExpanded"
          @click="ui.toggleRail()"
        >
          <IconPanelRight :size="15" />
          证据面板
        </button>
      </header>
      <div ref="scrollEl" class="ws__scroll" @scroll.passive="onScroll">
        <p v-if="bootstrapping || sessionStore.loadingMessages || changingSession" class="ws__loading" role="status">正在打开会话…</p>
        <div class="ws__thread">
          <!-- 作品说明：空状态 -->
          <div v-if="!hasConversation && !bootstrapping && !changingSession" class="ws__welcome">
            <div class="ws__welcome-mark"><IconSpark :size="22" /></div>
            <h2>问一个财报问题</h2>
            <p class="ws__welcome-intro">输入公司、报告期和想了解的指标，回答中的数字可在证据面板逐项核对。</p>
            <div v-if="examplesStore.examples.length" class="ws__welcome-examples">
              <div class="ws__welcome-label">试试这样问</div>
              <div class="ws__welcome-actions">
                <button
                  v-for="example in examplesStore.examples.slice(0, 2)"
                  :key="example.id"
                  type="button"
                  class="ws__welcome-action"
                  :disabled="busy"
                  @click="send(example.question)"
                >
                  <span>{{ example.question }}</span>
                  <IconChevronRight :size="16" />
                </button>
              </div>
            </div>
            <div class="ws__welcome-browse">
              <span class="ws__welcome-label">先了解公司与资料</span>
              <div class="ws__welcome-links">
                <router-link class="ws__welcome-link" to="/market">
                  <IconChart :size="16" />
                  浏览公司数据
                </router-link>
                <router-link class="ws__welcome-link" to="/reports">
                  <IconDocument :size="16" />
                  搜索资料库
                </router-link>
              </div>
            </div>
          </div>

          <!-- 作品说明：历史消息 -->
          <ChatMessage
            v-for="view in messageViews"
            :key="`${view.messageIndex}-${view.role}`"
            :view="view"
            :clarify-disabled="sessionStore.isStreaming || view.messageIndex !== messageViews.length - 1"
            @open-detail="openDetail"
            @retry="retryMessage"
            @select-clarify="send"
          />

          <!-- 作品说明：流式进行中 -->
          <template v-if="streamingViews">
            <ChatMessage :view="streamingViews.user" />
            <ChatMessage
              :view="streamingViews.assistant"
              :clarify-disabled="false"
              @open-detail="(tab) => ui.openRail(tab)"
              @select-clarify="send"
            />
          </template>
        </div>
      </div>

      <!-- 作品说明：输入区 -->
      <div class="ws__composer">
        <button v-if="hasConversation && !autoFollow" type="button" class="ws__latest" @click="autoFollow = true; scrollToBottom(true)">回到最新消息 ↓</button>
        <div v-if="ui.error" class="ws__error" role="alert">{{ ui.error }}</div>
        <ContextChips :context="sessionStore.latestContext" />
        <ChatInput :disabled="bootstrapping || changingSession || sessionStore.loadingMessages || sessionStore.streaming.holding" :streaming="sessionStore.isStreaming" @send="send" @stop="stop" />
        <p class="ws__hint">回答仅基于已发布的财报数据与研报原文，可在证据面板回查；不构成投资建议。</p>
      </div>
    </main>

    <!-- 作品说明：右侧证据栏（常驻可展开） -->
    <EvidenceRail :view="focusedView" />
  </div>
</template>

<style scoped>
.ws {
  display: grid;
  grid-template-columns: var(--rail-left-w) minmax(0, 1fr) auto;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: var(--c-bg);
}

.ws__topbar {
  display: none;
}

.ws__sidebar-head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); }
.ws__session-search { width: 100%; min-width: 0; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--r-full); background: var(--c-bg); font-size: var(--fs-sm); }
.ws__loading { padding: var(--sp-4); text-align: center; color: var(--c-text-secondary); }
.ws__latest { align-self: center; border: 1px solid var(--c-primary-border); background: var(--c-surface); color: var(--c-primary); border-radius: var(--r-full); padding: 6px 14px; cursor: pointer; }

/* 作品说明：左侧 */
.ws__left {
  width: var(--rail-left-w);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-height: 0;
  padding: var(--sp-4);
  border-right: 1px solid var(--c-border);
  background: var(--c-surface);
  overflow: hidden;
}

.ws__brand {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  color: var(--c-text);
}

.ws__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.ws__brand-text strong {
  font-size: var(--fs-md);
  font-weight: 700;
}

.ws__brand-text small {
  font-size: 11px;
  color: var(--c-text-tertiary);
}

.ws__brand:hover {
  text-decoration: none;
}

.ws__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
}

/* 作品说明：底部页面入口 */
.ws__links {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  border-top: 1px solid var(--c-border);
  padding-top: var(--sp-3);
}

.ws__link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px var(--sp-2);
  border-radius: var(--r-md);
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
  transition: background var(--t-fast), color var(--t-fast);
}

.ws__link:hover {
  text-decoration: none;
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.ws__section {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-height: 0;
}

.ws__section--sessions {
  flex: 1 1 200px;
  overflow: hidden;
}

.ws__examples {
  flex: 0 1 auto;
  max-height: 36%;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-top: 1px solid var(--c-border);
  padding-top: var(--sp-3);
  overflow-y: auto;
}

.ws__examples > summary {
  cursor: pointer;
  list-style: none;
  margin-bottom: var(--sp-1);
}

.ws__examples > summary::-webkit-details-marker {
  display: none;
}

.ws__examples > summary::after {
  content: " ▸";
}

.ws__examples[open] > summary::after {
  content: " ▾";
}

.ws__sessions {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 0;
}

.ws__session {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: background var(--t-fast);
}

.ws__session:hover {
  background: var(--c-bg);
}

.ws__session--active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 500;
}

.ws__session-title {
  flex: 1;
  min-width: 0;
  text-align: left;
  border: none;
  padding: 4px 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ws__session-del {
  flex-shrink: 0;
  display: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: var(--r-sm);
  background: none;
  color: var(--c-text-tertiary);
  cursor: pointer;
}

.ws__session:hover .ws__session-del,
.ws__session:focus-within .ws__session-del {
  display: inline-flex;
}

.ws__session-del:hover {
  background: var(--c-danger-soft);
  color: var(--c-danger);
}

.ws__sessions-empty {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  padding: var(--sp-2) var(--sp-3);
}

.ws__user {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}

.ws__user-name {
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ws__user-logout {
  border: none;
  background: none;
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
  cursor: pointer;
}

.ws__user-logout:hover {
  color: var(--c-danger);
}

.ws__section-title {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--c-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  padding: 0 var(--sp-3);
}

.ws__example {
  text-align: left;
  border: 1px solid transparent;
  background: none;
  border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--c-text-secondary);
  cursor: pointer;
  transition: all var(--t-fast);
}

.ws__example:hover:not(:disabled) {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.ws__example:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ws__left-footer {
  flex: 0 0 auto;
  border-top: 1px solid var(--c-border);
  padding-top: var(--sp-3);
  background: var(--c-surface);
}

/* 作品说明：中央标题栏 */
.ws__bar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  height: 52px;
  padding: 0 var(--sp-5);
  border-bottom: 1px solid var(--c-border);
  background: var(--c-surface);
}

.ws__bar-title {
  min-width: 0;
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ws__bar-btn {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 var(--sp-3);
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface);
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
}

.ws__bar-btn:hover {
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

.ws__bar-btn--active {
  border-color: var(--c-primary-border);
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

/* 作品说明：中央 */
.ws__center {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.ws__scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  touch-action: pan-y;
}

.ws__thread {
  max-width: var(--chat-max-w);
  margin: 0 auto;
  padding: var(--sp-6) var(--sp-5) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.ws__welcome {
  text-align: center;
  padding: var(--sp-8) var(--sp-4);
  color: var(--c-text-secondary);
}

.ws__welcome-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: var(--r-lg);
  background: var(--c-primary-soft);
  color: var(--c-primary);
  margin-bottom: var(--sp-4);
}

.ws__welcome h2 {
  font-size: var(--fs-xl);
  color: var(--c-text);
}

.ws__welcome-intro {
  max-width: 520px;
  margin: var(--sp-2) auto 0;
  font-size: var(--fs-sm);
  line-height: 1.7;
}

.ws__welcome-examples,
.ws__welcome-browse {
  margin: var(--sp-6) auto 0;
  max-width: 620px;
  text-align: left;
}

.ws__welcome-label {
  color: var(--c-text-tertiary);
  font-size: var(--fs-xs);
}

.ws__welcome-actions {
  margin-top: var(--sp-2);
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-3);
}

.ws__welcome-action {
  min-width: 0;
  min-height: 96px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 16px;
  align-items: start;
  gap: var(--sp-3);
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  border-radius: var(--r-md);
  padding: var(--sp-4);
  color: var(--c-text);
  text-align: left;
  font-size: var(--fs-sm);
  line-height: 1.7;
  overflow-wrap: anywhere;
  cursor: pointer;
  transition: border-color var(--t-fast), background var(--t-fast), color var(--t-fast);
}

.ws__welcome-action:hover {
  border-color: var(--c-primary-border);
  background: var(--c-primary-soft);
  color: var(--c-primary);
  text-decoration: none;
}

.ws__welcome-action > svg {
  margin-top: 3px;
  color: var(--c-text-tertiary);
}

.ws__welcome-action:disabled {
  cursor: wait;
  opacity: 0.6;
}

.ws__welcome-browse {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  column-gap: var(--sp-4);
  row-gap: var(--sp-2);
  padding-top: var(--sp-3);
  margin-top: var(--sp-4);
  border-top: 1px solid var(--c-border);
}

.ws__welcome-links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-5);
}

.ws__welcome-link {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-height: 36px;
  color: var(--c-primary);
  font-size: var(--fs-sm);
  white-space: nowrap;
}

/* 作品说明：输入区 */
.ws__composer {
  flex: 0 0 auto;
  max-width: var(--chat-max-w);
  width: 100%;
  margin: 0 auto;
  padding: var(--sp-2) var(--sp-5) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.ws__error {
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}

.ws__hint {
  text-align: center;
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

@media (max-width: 1200px) {
  .ws { grid-template-columns: var(--rail-left-w) minmax(0, 1fr); }
}

@media (max-width: 960px) {
  .ws {
    display: flex;
    flex-direction: column;
  }

  .ws__topbar {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    min-height: 54px;
    padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--c-border);
    background: var(--c-surface);
    z-index: 2;
  }

  .ws__topbar-brand {
    flex: 1;
    display: inline-flex;
    align-items: center;
    gap: var(--sp-2);
    color: var(--c-text);
    font-weight: 600;
    white-space: nowrap;
  }

  .ws__topbar-brand:hover {
    text-decoration: none;
  }

  .ws__topbar-new {
    flex: 0 0 auto;
    width: 34px;
    height: 34px;
    border: none;
    border-radius: var(--r-md);
    background: var(--c-primary);
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }

  .ws__topbar-new:disabled {
    opacity: 0.55;
  }

  .ws__left {
    width: 100%;
    height: 100%;
    padding-bottom: max(var(--sp-4), env(safe-area-inset-bottom));
  }

  .ws__session-del { display: inline-flex; width: 28px; height: 28px; }

  .ws__center {
    min-height: 0;
  }

  .ws__thread {
    padding: var(--sp-4) var(--sp-3);
  }

  .ws__composer {
    padding: var(--sp-2) var(--sp-3) var(--sp-3);
    padding-bottom: max(var(--sp-3), env(safe-area-inset-bottom));
  }
}

@media (max-width: 640px) {
  .ws__welcome-actions {
    grid-template-columns: 1fr;
  }
}
</style>
