import { createRouter, createWebHistory } from "vue-router";

import { loadStoredAuth } from "@/services/api";

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition;
    if (to.path === from.path) return false;
    return { top: 0 };
  },
  routes: [
    {
      path: "/",
      name: "home",
      component: () => import("@/pages/HomePage.vue"),
    },
    {
      path: "/login",
      name: "login",
      component: () => import("@/pages/LoginPage.vue"),
      meta: { guestOnly: true },
    },
    {
      path: "/market",
      name: "market",
      component: () => import("@/pages/MarketPage.vue"),
    },
    {
      path: "/market/:code",
      name: "company-detail",
      component: () => import("@/pages/CompanyDetailPage.vue"),
    },
    {
      path: "/reports",
      name: "reports",
      component: () => import("@/pages/ReportsPage.vue"),
    },
    {
      path: "/profile",
      name: "profile",
      component: () => import("@/pages/ProfilePage.vue"),
      meta: { requiresAuth: true },
    },
    {
      path: "/financial-submissions",
      name: "financial-submissions",
      component: () => import("@/pages/FinancialSubmissionsPage.vue"),
      meta: { requiresAuth: true },
    },
    {
      path: "/admin/financial-submissions",
      name: "admin-financial-submissions",
      component: () => import("@/pages/FinancialSubmissionsPage.vue"),
      props: { admin: true },
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: "/admin",
      name: "admin",
      component: () => import("@/pages/AdminPage.vue"),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: "/workspace",
      name: "workspace",
      component: () => import("@/pages/WorkspacePage.vue"),
      meta: { requiresAuth: true },
    },
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: () => import("@/pages/NotFoundPage.vue"),
    },
  ],
});

router.beforeEach((to) => {
  const stored = loadStoredAuth();
  const authed = stored !== null;
  if (to.meta.requiresAuth && !authed) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (to.meta.requiresAdmin && stored?.user.role !== "ADMIN") {
    return { name: "market" };
  }
  if (to.meta.guestOnly && authed) {
    return { name: "workspace" };
  }
  return true;
});

export default router;
