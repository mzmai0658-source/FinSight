<script setup lang="ts">
import PortalShell from '@/components/layout/PortalShell.vue';
</script>

<template>
  <PortalShell>
    <section class="not-found">
      <span class="not-found__code">404</span>
      <h1>页面不存在</h1>
      <p>这个地址可能有误，或页面已经移动。你可以返回公司数据继续浏览。</p>
      <div class="not-found__links">
        <router-link to="/market">浏览公司数据</router-link>
        <router-link to="/">返回首页</router-link>
      </div>
    </section>
  </PortalShell>
</template>

<style scoped>
.not-found { margin: clamp(24px, 8vh, 100px) auto; max-width: 540px; text-align: center; padding: 24px 12px; }
.not-found__code { color: var(--c-primary); font-size: 48px; font-weight: 800; }
.not-found h1 { margin-top: 12px; color: var(--c-ink); font-size: var(--fs-2xl); }
.not-found p { margin: 12px 0 24px; color: var(--c-text-secondary); line-height: 1.8; }
.not-found__links { display: flex; justify-content: center; flex-wrap: wrap; gap: 12px; }
.not-found__links a { border: 1px solid var(--c-primary-border); border-radius: var(--r-full); padding: 10px 18px; color: var(--c-primary); background: var(--c-primary-soft); }
</style>
