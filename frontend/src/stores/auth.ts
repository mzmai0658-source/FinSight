import { defineStore } from "pinia";

import {
  adminLogin,
  fetchMe,
  loadStoredAuth,
  login,
  logout,
  register,
  saveStoredAuth,
  setAuthExpiredHandler,
} from "@/services/api";
import router from "@/router";
import type { AuthUser } from "@/types/api";

interface AuthState {
  user: AuthUser | null;
  initialized: boolean;
}

export const useAuthStore = defineStore("auth", {
  state: (): AuthState => ({
    user: null,
    initialized: false,
  }),
  getters: {
    isAuthenticated: (state) => state.user !== null,
    isAdmin: (state) => state.user?.role === "ADMIN",
  },
  actions: {
    /** 作品说明：应用启动时恢复登录态，并注册全局 401 过期跳转 */
    init() {
      if (this.initialized) return;
      this.initialized = true;
      setAuthExpiredHandler(() => {
        this.user = null;
        const current = router.currentRoute.value;
        if (current.meta.requiresAuth) {
          router.push({ name: "login", query: { redirect: current.fullPath } });
        }
      });
      const stored = loadStoredAuth();
      if (stored) {
        this.user = stored.user;
        // 作品说明：后台校验 token 仍有效（失败时拦截器会触发刷新/登出）
        fetchMe()
          .then((user) => {
            this.user = user;
            const latest = loadStoredAuth();
            if (latest) saveStoredAuth({ ...latest, user });
          })
          .catch(() => {
            // 作品说明：刷新失败场景由 authExpiredHandler 收尾
          });
      }
    },
    async login(username: string, password: string) {
      const pair = await login({ username, password });
      this.user = pair.user;
    },
    async adminLogin(username: string, password: string) {
      const pair = await adminLogin({ username, password });
      this.user = pair.user;
    },
    async register(username: string, password: string, nickname?: string) {
      await register({ username, password, nickname });
      await this.login(username, password);
    },
    async logout() {
      await logout();
      this.user = null;
      router.push({ name: "login" });
    },
  },
});
