<script setup lang="ts">
import PortalNav from "@/components/market/PortalNav.vue";

withDefaults(defineProps<{ footer?: boolean }>(), { footer: true });
</script>

<template>
  <div class="portal">
    <PortalNav />
    <main class="portal__main">
      <slot />
    </main>
    <footer v-if="footer" class="portal__footer">
      <div class="portal__footer-inner">
        <div class="portal__footer-brand">
          <strong>FinSight</strong>
          <span>面向财报学习与研究的证据助手</span>
        </div>
        <p class="portal__footer-note">
          数字答案只来自已发布的结构化财报，并附公司、报告期与原始页证据；证据缺失或来源冲突时明确说明，不以其他期间或零值替代。本站内容不构成投资建议。
        </p>
        <nav class="portal__footer-links" aria-label="页脚导航">
          <router-link to="/market">公司数据</router-link>
          <router-link to="/reports">资料库</router-link>
          <router-link to="/workspace">AI 工作台</router-link>
        </nav>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.portal {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--c-bg);
}

.portal__main {
  flex: 1 0 auto;
  min-width: 0;
  width: 100%;
  max-width: var(--page-max-w);
  margin: 0 auto;
  padding: var(--sp-8) var(--sp-6) var(--sp-12);
  display: flex;
  flex-direction: column;
  gap: var(--portal-content-gap);
}

.portal__main > :deep(*) { min-width: 0; }

.portal__footer {
  flex: 0 0 auto;
  border-top: 1px solid var(--c-border);
  background: var(--c-surface);
}

.portal__footer-inner {
  max-width: var(--page-max-w);
  margin: 0 auto;
  padding: var(--sp-6);
  display: grid;
  grid-template-columns: minmax(180px, auto) minmax(0, 1fr) auto;
  gap: var(--sp-6);
  align-items: start;
}

.portal__footer-brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.portal__footer-brand strong {
  font-size: var(--fs-md);
  color: var(--c-ink);
}

.portal__footer-brand span,
.portal__footer-note {
  font-size: var(--fs-xs);
  line-height: 1.7;
  color: var(--c-text-tertiary);
}

.portal__footer-note {
  max-width: 640px;
}

.portal__footer-links {
  display: flex;
  gap: var(--sp-4);
  font-size: var(--fs-xs);
}

.portal__footer-links a {
  color: var(--c-text-secondary);
}

.portal__footer-links a:hover {
  color: var(--c-primary);
}

@media (max-width: 900px) {
  .portal__footer-inner {
    grid-template-columns: 1fr;
    gap: var(--sp-3);
  }
}

@media (max-width: 640px) {
  .portal__main {
    padding: var(--sp-5) var(--sp-4) var(--sp-10);
    gap: var(--sp-5);
  }

  .portal__footer-inner {
    padding: var(--sp-5) var(--sp-4);
  }
}
</style>
