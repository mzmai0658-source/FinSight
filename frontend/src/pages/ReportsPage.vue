<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

import PageHeader from "@/components/layout/PageHeader.vue";
import PortalShell from "@/components/layout/PortalShell.vue";
import FinancialLibrary from "@/components/report/FinancialLibrary.vue";
import InstitutionLibrary from "@/components/report/InstitutionLibrary.vue";
import { IconDocument, IconList } from "@/components/ui/icons";
import { loadStoredAuth } from "@/services/api";

type Tab = "financial" | "institution";

const route = useRoute();
const router = useRouter();

function initialTab(): Tab {
  const query = route.query.tab;
  if (query === "financial" || query === "institution") return query;
  // 作品说明：财报原件需要登录才能查看；未登录时先展示公开的机构研报
  return loadStoredAuth() ? "financial" : "institution";
}

const tab = computed<Tab>({
  get: initialTab,
  set: (value) => { void router.push({ query: { ...route.query, tab: value } }); },
});
const financialKeyword = computed(() => typeof route.query.company === "string" ? route.query.company : "");
function searchFinancial(keyword: string) {
  void router.replace({ query: { ...route.query, tab: "financial", company: keyword || undefined } });
}

const tabs = [
  { value: "financial" as const, label: "公司财报", desc: "年报、半年报、季报原件与正文", icon: IconDocument },
  { value: "institution" as const, label: "机构研报", desc: "个股与行业研究报告", icon: IconList },
];

</script>

<template>
  <PortalShell>
    <PageHeader
      title="资料库"
      description="按公司与报告期查阅已收录的财报原件和正文，或浏览机构研究报告。正文来自 PDF 文字提取或 OCR，可能存在识别误差，核对数字时请以原件为准。"
    />

    <div class="materials">
      <nav class="material-tabs" aria-label="资料类型">
        <button
          v-for="item in tabs"
          :key="item.value"
          type="button"
          class="material-tab"
          :class="{ 'material-tab--active': tab === item.value }"
          :aria-pressed="tab === item.value"
          @click="tab = item.value"
        >
          <span class="material-tab__icon"><component :is="item.icon" :size="16" /></span>
          <span class="material-tab__text">
            <strong>{{ item.label }}</strong>
            <small>{{ item.desc }}</small>
          </span>
        </button>
      </nav>

      <div class="materials__panel">
        <KeepAlive>
          <FinancialLibrary v-if="tab === 'financial'" :initial-keyword="financialKeyword" @search="searchFinancial" />
          <InstitutionLibrary v-else />
        </KeepAlive>
      </div>
    </div>
  </PortalShell>
</template>

<style scoped>
.materials {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.material-tabs {
  display: flex;
  gap: var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}

.material-tab {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-bottom: -1px;
  padding: var(--sp-3) var(--sp-4) var(--sp-3) 0;
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--c-text-secondary);
  text-align: left;
  cursor: pointer;
  transition: color var(--t-fast), border-color var(--t-fast);
}

.material-tab + .material-tab {
  padding-left: var(--sp-4);
}

.material-tab__icon {
  width: 32px;
  height: 32px;
  border-radius: var(--r-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  color: var(--c-text-tertiary);
}

.material-tab__text {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.material-tab__text strong {
  font-size: var(--fs-md);
  font-weight: 600;
}

.material-tab__text small {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.material-tab:hover {
  color: var(--c-ink);
}

.material-tab--active {
  color: var(--c-ink);
  border-bottom-color: var(--c-primary);
}

.material-tab--active .material-tab__icon {
  background: var(--c-primary-soft);
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

@media (max-width: 640px) {
  .material-tab__text small,
  .material-tab__icon {
    display: none;
  }

  .material-tab {
    flex: 1;
    justify-content: center;
    padding: var(--sp-3) 0;
  }

  .material-tab + .material-tab {
    padding-left: 0;
  }
}
</style>
