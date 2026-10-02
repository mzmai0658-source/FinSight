<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { useRoute } from "vue-router";

import AppBadge from "@/components/ui/AppBadge.vue";
import { IconMenu, IconSpark } from "@/components/ui/icons";
import { fetchUnreadCount } from "@/services/api";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const unread = ref(0);
const mobileOpen = ref(false);
watch(() => route.fullPath, () => { mobileOpen.value = false; });

onMounted(async () => {
  if (!auth.isAuthenticated) return;
  try {
    unread.value = await fetchUnreadCount();
  } catch {
    // 作品说明：关注列表加载失败时保留当前导航，避免阻断页面。
  }
});

const navItems = [
  { to: "/market", label: "公司数据" },
  { to: "/reports", label: "资料库" },
];

const workspaceItem = { to: "/workspace", label: "AI 工作台" };
const adminItem = { to: "/admin", label: "管理端" };
</script>

<template>
  <header class="pnav" @keydown.esc="mobileOpen = false">
    <div class="pnav__inner">
      <router-link class="pnav__brand" to="/">
        <span class="pnav__brand-mark"><IconSpark :size="16" /></span>
        <span class="pnav__brand-text">
          <strong>FinSight</strong>
          <small>财报证据助手</small>
        </span>
      </router-link>

      <nav class="pnav__links" aria-label="主导航">
        <router-link
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="pnav__link"
          :class="{ 'pnav__link--active': route.path.startsWith(item.to) }"
          active-class="pnav__link--active"
        >
          {{ item.label }}
        </router-link>
        <router-link
          v-if="auth.user?.role === 'ADMIN'"
          :to="adminItem.to"
          class="pnav__link"
          active-class="pnav__link--active"
        >
          {{ adminItem.label }}
        </router-link>
      </nav>

      <div class="pnav__right">
        <router-link class="pnav__cta" :to="workspaceItem.to">
          <IconSpark :size="14" />
          {{ workspaceItem.label }}
        </router-link>
        <router-link v-if="auth.isAuthenticated" class="pnav__user" to="/profile" title="个人中心">
          <span class="pnav__avatar">{{ (auth.user?.nickname || auth.user?.username || "?").slice(0, 1) }}</span>
          <span class="pnav__user-name">{{ auth.user?.nickname || auth.user?.username }}</span>
          <AppBadge v-if="unread > 0" variant="danger" dot size="sm">{{ unread > 99 ? "99+" : unread }}</AppBadge>
        </router-link>
        <router-link v-else class="pnav__login" :to="{ path: '/login', query: { redirect: route.fullPath } }">登录</router-link>

        <button
          type="button"
          class="pnav__menu"
          aria-label="菜单"
          :aria-expanded="mobileOpen"
          @click="mobileOpen = !mobileOpen"
        >
          <IconMenu :size="20" />
        </button>
      </div>
    </div>

    <!-- 作品说明：移动端抽屉 -->
    <div v-if="mobileOpen" class="pnav__drawer" @click="mobileOpen = false">
      <router-link
        v-for="item in [...navItems, workspaceItem, ...(auth.user?.role === 'ADMIN' ? [adminItem] : [])]"
        :key="item.to"
        :to="item.to"
        class="pnav__drawer-link"
        active-class="pnav__drawer-link--active"
      >
        {{ item.label }}
      </router-link>
      <router-link v-if="!auth.isAuthenticated" class="pnav__drawer-link" :to="{ path: '/login', query: { redirect: route.fullPath } }">登录 / 注册</router-link>
      <router-link v-else class="pnav__drawer-link" to="/profile">个人中心</router-link>
    </div>
  </header>
</template>

<style scoped>
.pnav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgb(255 255 255 / 92%);
  backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--c-border);
}

.pnav__inner {
  max-width: var(--page-max-w);
  height: var(--portal-nav-h);
  margin: 0 auto;
  padding: 0 var(--sp-6);
  display: flex;
  align-items: center;
  gap: var(--sp-8);
}

.pnav__brand {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex: 0 0 auto;
  color: var(--c-ink);
}

.pnav__brand:hover {
  text-decoration: none;
}

.pnav__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--r-md);
  background: var(--c-primary);
  color: #fff;
}

.pnav__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.pnav__brand-text strong {
  font-size: var(--fs-md);
  font-weight: 700;
}

.pnav__brand-text small {
  font-size: 11px;
  color: var(--c-text-tertiary);
  letter-spacing: 0.02em;
}

.pnav__links {
  display: flex;
  align-self: stretch;
  gap: var(--sp-6);
  flex: 1;
  min-width: 0;
}

.pnav__link {
  position: relative;
  display: inline-flex;
  align-items: center;
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  white-space: nowrap;
  transition: color var(--t-fast);
}

.pnav__link::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  border-radius: 2px;
  background: transparent;
  transition: background var(--t-fast);
}

.pnav__link:hover {
  color: var(--c-ink);
  text-decoration: none;
}

.pnav__link--active {
  color: var(--c-ink);
  font-weight: 600;
}

.pnav__link--active::after {
  background: var(--c-primary);
}

.pnav__right {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex: 0 0 auto;
}

.pnav__cta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 var(--sp-4);
  border-radius: var(--r-full);
  background: var(--c-primary);
  color: #fff;
  font-size: var(--fs-sm);
  font-weight: 500;
  transition: background var(--t-fast);
}

.pnav__cta:hover {
  background: var(--c-primary-hover);
  text-decoration: none;
}

.pnav__user {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  height: 34px;
  padding: 0 var(--sp-3) 0 4px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface);
  transition: border-color var(--t-fast);
}

.pnav__user:hover {
  border-color: var(--c-primary-border);
  text-decoration: none;
}

.pnav__avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-size: var(--fs-xs);
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.pnav__user-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.pnav__login {
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 var(--sp-4);
  border: 1px solid var(--c-border-strong);
  border-radius: var(--r-full);
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-ink);
  background: var(--c-surface);
}

.pnav__login:hover {
  border-color: var(--c-primary-border);
  color: var(--c-primary);
  text-decoration: none;
}

.pnav__menu {
  display: none;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  background: var(--c-surface);
  color: var(--c-text-secondary);
  cursor: pointer;
}

.pnav__drawer {
  position: absolute;
  top: var(--portal-nav-h);
  left: 0;
  right: 0;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
  padding: var(--sp-3) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  box-shadow: var(--shadow-md);
}

.pnav__drawer-link {
  padding: var(--sp-3);
  border-radius: var(--r-md);
  color: var(--c-text-secondary);
  font-size: var(--fs-md);
}

.pnav__drawer-link:hover,
.pnav__drawer-link--active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  text-decoration: none;
}

@media (max-width: 800px) {
  .pnav__inner {
    padding: 0 var(--sp-4);
    gap: var(--sp-3);
  }

  .pnav__links,
  .pnav__cta,
  .pnav__user-name {
    display: none;
  }

  .pnav__brand {
    flex: 1;
  }

  .pnav__menu {
    display: inline-flex;
  }

  .pnav__user {
    padding-right: 4px;
  }
}
</style>
