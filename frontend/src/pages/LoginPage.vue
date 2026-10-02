<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppButton from "@/components/ui/AppButton.vue";
import { IconSpark } from "@/components/ui/icons";
import { useAuthStore } from "@/stores/auth";

type Mode = "login" | "register" | "admin";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const mode = ref<Mode>("login");
const username = ref("");
const password = ref("");
const confirmPassword = ref("");
const nickname = ref("");
const submitting = ref(false);
const error = ref("");

const isLogin = computed(() => mode.value === "login");
const isRegister = computed(() => mode.value === "register");
const isAdmin = computed(() => mode.value === "admin");

const title = computed(() =>
  isAdmin.value ? "管理员登录" : isRegister.value ? "注册账号" : "欢迎回来",
);

const subtitle = computed(() =>
  isRegister.value ? "创建账号后即可提问、查阅财报原件并保存对话" : "登录后继续使用财报证据问答与资料库",
);

function switchMode(next: Mode) {
  mode.value = next;
  error.value = "";
}

async function submit() {
  error.value = "";
  if (!username.value.trim() || !password.value) {
    error.value = "请输入用户名和密码";
    return;
  }
  if (isRegister.value) {
    if (password.value.length < 6) {
      error.value = "密码至少 6 位";
      return;
    }
    if (password.value !== confirmPassword.value) {
      error.value = "两次输入的密码不一致";
      return;
    }
  }
  submitting.value = true;
  try {
    if (isLogin.value) {
      await auth.login(username.value.trim(), password.value);
    } else if (isRegister.value) {
      await auth.register(username.value.trim(), password.value, nickname.value.trim() || undefined);
    } else {
      await auth.adminLogin(username.value.trim(), password.value);
    }
    const redirect = String(route.query.redirect ?? "");
    router.push(redirect && redirect.startsWith("/") ? redirect : { name: "workspace" });
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败，请稍后重试";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="login">
    <div class="login__left">
      <router-link class="login__brand" to="/">
        <span class="login__brand-mark"><IconSpark :size="20" /></span>
        FinSight
      </router-link>

      <div class="login__hero">
        <span class="login__kicker">财报证据助手</span>
        <h1>让每个财务数字<br />回到原始证据</h1>
        <p>
          面向财报学习与研究：数字答案绑定公司、报告期和原始页码，证据不足时明确说明，不做推测。
        </p>

        <ol class="login__features">
          <li v-for="(item, idx) in ['锁定公司与报告期', '只读 SQL 取数并核验', '附原始 PDF 与页码']" :key="item" class="login__feature">
            <span class="login__feature-num">{{ idx + 1 }}</span>
            <span>{{ item }}</span>
          </li>
        </ol>
      </div>

      <footer class="login__footer">FinSight · 仅用于学习与研究，不构成投资建议</footer>
    </div>

    <div class="login__right">
      <header class="login__mobile-head">
        <router-link class="login__mobile-brand" to="/"><span class="login__brand-mark"><IconSpark :size="18" /></span>FinSight</router-link>
        <router-link to="/market">先浏览公司数据</router-link>
      </header>
      <div class="login__card">
        <div class="login__header">
          <h2 class="login__title">{{ title }}</h2>
          <p class="login__subtitle">{{ subtitle }}</p>
        </div>

        <form class="login__form" @submit.prevent="submit">
          <label class="login__field">
            <span>用户名</span>
            <input
              v-model="username"
              type="text"
              autocomplete="username"
              placeholder="字母、数字、下划线"
              :disabled="submitting"
            />
          </label>

          <label v-if="isRegister" class="login__field">
            <span>昵称（可选）</span>
            <input v-model="nickname" type="text" placeholder="显示名称" :disabled="submitting" />
          </label>

          <label class="login__field">
            <span>密码</span>
            <input
              v-model="password"
              type="password"
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
              placeholder="至少 6 位"
              :disabled="submitting"
            />
          </label>

          <label v-if="isRegister" class="login__field">
            <span>确认密码</span>
            <input
              v-model="confirmPassword"
              type="password"
              autocomplete="new-password"
              placeholder="再次输入密码"
              :disabled="submitting"
            />
          </label>

          <div v-if="error" class="login__error" role="alert">{{ error }}</div>

          <AppButton variant="primary" block size="lg" type="submit" :loading="submitting">
            {{ submitting ? "请稍候…" : isLogin ? "登录" : isRegister ? "注册" : "管理员登录" }}
          </AppButton>
        </form>

        <div class="login__links">
          <template v-if="isLogin">
            <button type="button" class="login__link" @click="switchMode('register')">
              没有账号？注册
            </button>
            <button type="button" class="login__link" @click="switchMode('admin')">
              管理员入口
            </button>
          </template>
          <template v-else>
            <button type="button" class="login__link" @click="switchMode('login')">
              返回用户登录
            </button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(420px, 1.1fr) minmax(420px, 0.9fr);
}
.login__mobile-head { display: none; }

.login__left {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: var(--sp-8);
  background: linear-gradient(145deg, var(--c-primary) 0%, #173b8a 100%);
  color: #fff;
  overflow: hidden;
}

.login__left::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 20% 30%, rgb(255 255 255 / 8%) 0%, transparent 40%),
    radial-gradient(circle at 80% 70%, rgb(255 255 255 / 6%) 0%, transparent 45%);
  pointer-events: none;
}

.login__brand {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  font-weight: 700;
  font-size: var(--fs-lg);
  color: #fff;
  z-index: 1;
}

.login__brand:hover {
  text-decoration: none;
}

.login__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--r-md);
  background: rgb(255 255 255 / 18%);
  backdrop-filter: blur(4px);
}

.login__hero {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  max-width: 520px;
  z-index: 1;
}

.login__hero h1 {
  font-size: var(--fs-3xl);
  font-weight: 800;
  line-height: 1.18;
  letter-spacing: -0.5px;
}

.login__hero p {
  margin-top: var(--sp-4);
  font-size: var(--fs-lg);
  line-height: 1.75;
  color: rgb(255 255 255 / 78%);
}

.login__kicker {
  margin-bottom: var(--sp-3);
  font-size: var(--fs-xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  color: rgb(255 255 255 / 70%);
}

.login__features {
  margin: var(--sp-8) 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.login__feature {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  font-size: var(--fs-md);
  color: rgb(255 255 255 / 86%);
}

.login__feature-num {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgb(255 255 255 / 35%);
  background: rgb(255 255 255 / 10%);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.login__footer {
  position: relative;
  font-size: var(--fs-xs);
  color: rgb(255 255 255 / 50%);
  z-index: 1;
}

.login__right {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sp-8);
  background: var(--c-bg);
}

.login__card {
  width: 100%;
  max-width: 420px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-lg);
  padding: var(--sp-8);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.login__header {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}

.login__title {
  font-size: var(--fs-xl);
  font-weight: 700;
}

.login__subtitle {
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.login__form {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.login__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login__field span {
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--c-text-secondary);
}

.login__field input {
  height: 42px;
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  padding: 0 var(--sp-3);
  font-size: var(--fs-sm);
  background: var(--c-bg);
  color: var(--c-text);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

.login__field input:focus {
  outline: none;
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px var(--c-primary-soft);
}

.login__error {
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-sm);
}

.login__links {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-2);
}

.login__link {
  border: none;
  background: none;
  color: var(--c-primary);
  font-size: var(--fs-sm);
  cursor: pointer;
  padding: 0;
  font-weight: 500;
}

.login__link:hover {
  text-decoration: underline;
}

@media (max-width: 900px) {
  .login {
    grid-template-columns: 1fr;
  }

  .login__left {
    display: none;
  }

  .login__right {
    position: relative;
    padding: 84px var(--sp-4) var(--sp-8);
  }

  .login__mobile-head { position: absolute; top: 0; left: 0; right: 0; display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); padding: var(--sp-4); border-bottom: 1px solid var(--c-border); background: var(--c-surface); font-size: var(--fs-xs); }
  .login__mobile-brand { display: inline-flex; align-items: center; gap: var(--sp-2); color: var(--c-ink); font-size: var(--fs-md); font-weight: 700; }
  .login__mobile-brand .login__brand-mark { background: var(--c-primary); color: #fff; }

  .login__card {
    padding: var(--sp-4);
    box-shadow: none;
    border: none;
    background: transparent;
  }
}
</style>
