<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import ChatInput from "@/components/chat/ChatInput.vue";
import PortalShell from "@/components/layout/PortalShell.vue";
import {
  IconAlert,
  IconChart,
  IconCheck,
  IconChevronRight,
  IconClose,
  IconDatabase,
  IconDocument,
  IconList,
  IconSearch,
  IconSpark,
} from "@/components/ui/icons";
import { fetchMarketSummary } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import { useExamplesStore } from "@/stores/examples";
import type { MarketSummary } from "@/types/market";

const router = useRouter();
const examplesStore = useExamplesStore();
const auth = useAuthStore();
const draft = ref("");
const summary = ref<MarketSummary | null>(null);

/** 作品说明：一个数字答案从提问到可回查的四个环节 */
const evidenceSteps = [
  {
    icon: IconSearch,
    title: "锁定公司与报告期",
    desc: "按股票代码、年份和报告期匹配，不用其他期间替代。",
  },
  {
    icon: IconDatabase,
    title: "查阅已收录的财报",
    desc: "从对应报告中提取指标，保留数字的来源。",
  },
  {
    icon: IconCheck,
    title: "核对单位与口径",
    desc: "确认金额单位和报告期，遇到缺失或冲突会说明。",
  },
  {
    icon: IconDocument,
    title: "回到原始财报",
    desc: "附原始 PDF 与页码，图表逐点可回查。",
  },
];

const entryCards = computed(() => [
  {
    to: "/market",
    title: "公司数据",
    desc: "查看已入库公司的核心指标与趋势，从排名或列表进入详情。",
    icon: IconChart,
  },
  {
    to: "/reports",
    title: "资料库",
    desc: "按公司与报告期查阅财报原件、正文和机构研报。",
    icon: IconDocument,
  },
  {
    to: "/workspace",
    title: "AI 工作台",
    desc: "用自然语言提问，回答附 SQL、图表和原文证据。",
    icon: IconSpark,
  },
  auth.user?.role === "ADMIN"
    ? {
        to: "/admin",
        title: "管理端",
        desc: "导入财报与研报，查看处理任务、用户和对话审计。",
        icon: IconDatabase,
      }
    : {
        to: "/profile",
        title: "个人中心",
        desc: "维护关注的公司，查看数据更新通知。",
        icon: IconList,
      },
]);

const willDo = [
  "回答已发布报告期的财务数字，并标明公司、报告期和单位",
  "绘制趋势图，每个数据点都能回到对应财报",
  "引用财报或研报原文时注明文件与位置",
];

const wontDo = [
  "用其他期间的数据或零值填补缺失字段",
  "发布来源存在严重冲突的报告期",
  "给出投资建议或预测股价",
];

const yearSpan = computed(() => {
  const years = (summary.value?.yearlyAggregate ?? [])
    .map((row) => Number(row.year))
    .filter((year) => Number.isFinite(year));
  if (!years.length) return null;
  const min = Math.min(...years);
  const max = Math.max(...years);
  return min === max ? String(min) : `${min}–${max}`;
});

const scopeItems = computed(() => {
  if (!summary.value) return [];
  const s = summary.value;
  return [
    { value: s.importedCompanies, unit: "家", label: "公司已结构化入库" },
    { value: s.importedCompanies + s.pendingCompanies, unit: "家", label: "公司已收录" },
    { value: yearSpan.value ?? "—", unit: "", label: "年报年度" },
    { value: s.latestYear ?? "—", unit: "", label: "最新年报" },
  ];
});

onMounted(async () => {
  examplesStore.ensureLoaded();
  try {
    summary.value = await fetchMarketSummary();
  } catch {
    summary.value = null;
  }
});

function startChat(question: string) {
  const target = question ? `/workspace?q=${encodeURIComponent(question)}` : "/workspace";
  if (!auth.isAuthenticated) {
    router.push({ name: "login", query: { redirect: target } });
    return;
  }
  router.push(target);
}
</script>

<template>
  <PortalShell>
    <!-- 作品说明：首屏：提问 + 证据链 -->
    <section class="hero">
      <div class="hero__content">
        <span class="hero__eyebrow">面向财报学习与研究</span>
        <h1 class="hero__title">每个财务数字，<br />都能回到原始财报</h1>
        <p class="hero__subtitle">
          说出公司、报告期和想了解的指标，查看财务数字与趋势，再回到原始财报核对。还没选好公司？可以先浏览下方的公司数据和资料库。
        </p>

        <div class="hero__input">
          <ChatInput
            v-model="draft"
            placeholder="例如：某公司 2024 年全年营业收入是多少？请给出原始财报证据。"
            @send="startChat"
          />
        </div>

        <div v-if="examplesStore.examples.length" class="hero__examples">
          <span class="hero__examples-label">试试这些问题</span>
          <button
            v-for="example in examplesStore.examples.slice(0, 3)"
            :key="example.id"
            type="button"
            class="hero__example"
            :title="example.question"
            @click="startChat(example.question)"
          >
            {{ example.question }}
          </button>
        </div>
      </div>

      <aside class="chain" aria-label="一个回答如何产生">
        <header class="chain__head">
          <span class="chain__kicker">证据链</span>
          <h2>一个数字答案如何产生</h2>
        </header>
        <ol class="chain__steps">
          <li v-for="(step, idx) in evidenceSteps" :key="step.title" class="chain__step">
            <span class="chain__marker">
              <component :is="step.icon" :size="15" />
            </span>
            <div class="chain__body">
              <strong><span class="chain__num">0{{ idx + 1 }}</span>{{ step.title }}</strong>
              <span>{{ step.desc }}</span>
            </div>
          </li>
        </ol>
        <footer class="chain__foot">
          <IconAlert :size="14" />
          任一环节证据不足：明确告知缺什么，不输出数字。
        </footer>
      </aside>
    </section>

    <!-- 作品说明：当前数据范围（实时） -->
    <section v-if="scopeItems.length" class="scope" aria-label="当前数据范围">
      <div class="scope__intro">
        <strong>当前数据范围</strong>
        <span>数字问答只覆盖已通过发布检查的公司与报告期</span>
      </div>
      <dl class="scope__items">
        <div v-for="item in scopeItems" :key="item.label" class="scope__item">
          <dt>{{ item.label }}</dt>
          <dd>{{ item.value }}<small v-if="item.unit">{{ item.unit }}</small></dd>
        </div>
      </dl>
      <router-link class="scope__link" to="/market">
        查看公司数据 <IconChevronRight :size="14" />
      </router-link>
    </section>

    <!-- 作品说明：功能入口 -->
    <section class="entries">
      <h2 class="section-title">从这里开始</h2>
      <div class="entries__grid">
        <router-link v-for="card in entryCards" :key="card.to" :to="card.to" class="entry">
          <span class="entry__icon"><component :is="card.icon" :size="18" /></span>
          <strong class="entry__title">{{ card.title }}</strong>
          <span class="entry__desc">{{ card.desc }}</span>
          <span class="entry__go">进入 <IconChevronRight :size="14" /></span>
        </router-link>
      </div>
    </section>

    <!-- 作品说明：能力边界 -->
    <section class="bounds">
      <h2 class="section-title">系统的边界</h2>
      <div class="bounds__grid">
        <div class="bounds__col bounds__col--do">
          <h3><span class="bounds__badge"><IconCheck :size="13" /></span>会做</h3>
          <ul>
            <li v-for="item in willDo" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="bounds__col bounds__col--dont">
          <h3><span class="bounds__badge"><IconClose :size="13" /></span>不会做</h3>
          <ul>
            <li v-for="item in wontDo" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>
    </section>
  </PortalShell>
</template>

<style scoped>
.section-title {
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-ink);
  margin-bottom: var(--sp-4);
}

/* 作品说明：首屏 */
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.9fr);
  gap: var(--sp-10);
  align-items: center;
  padding: var(--sp-6) 0 var(--sp-4);
}

.hero__content {
  min-width: 0;
}

.hero__eyebrow {
  display: inline-block;
  font-size: var(--fs-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--c-primary);
  margin-bottom: var(--sp-3);
}

.hero__title {
  font-size: 42px;
  font-weight: 800;
  line-height: 1.18;
  letter-spacing: -0.8px;
  color: var(--c-ink);
}

.hero__subtitle {
  margin-top: var(--sp-4);
  max-width: 600px;
  font-size: var(--fs-lg);
  line-height: 1.75;
  color: var(--c-text-secondary);
}

.hero__input {
  margin-top: var(--sp-6);
  max-width: 640px;
}

.hero__examples {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-4);
  max-width: 640px;
}

.hero__examples-label {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  margin-right: var(--sp-1);
}

.hero__example {
  max-width: 100%;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  border-radius: var(--r-full);
  padding: 5px var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-secondary);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: border-color var(--t-fast), color var(--t-fast);
}

.hero__example:hover {
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

/* 作品说明：证据链 */
.chain {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-md);
  padding: var(--sp-6);
}

.chain__kicker {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--c-primary);
}

.chain__head h2 {
  margin-top: 2px;
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-ink);
}

.chain__steps {
  list-style: none;
  margin: var(--sp-5) 0 0;
  padding: 0;
}

.chain__step {
  position: relative;
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: var(--sp-3);
  padding-bottom: var(--sp-4);
}

.chain__step:not(:last-child)::before {
  content: "";
  position: absolute;
  left: 15px;
  top: 32px;
  bottom: 0;
  width: 2px;
  background: var(--c-primary-soft);
}

.chain__marker {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  border: 1px solid var(--c-primary-border);
}

.chain__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 5px;
  min-width: 0;
}

.chain__body strong {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--c-ink);
}

.chain__num {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--c-text-tertiary);
  margin-right: var(--sp-2);
}

.chain__body span:not(.chain__num) {
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--c-text-secondary);
}

.chain__foot {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-top: var(--sp-1);
  padding: var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-warning-soft);
  color: var(--c-warning);
  font-size: var(--fs-xs);
  line-height: 1.5;
}

/* 作品说明：数据范围 */
.scope {
  display: grid;
  grid-template-columns: minmax(200px, 0.8fr) minmax(0, 2fr) auto;
  gap: var(--sp-6);
  align-items: center;
  padding: var(--sp-5) var(--sp-6);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.scope__intro {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.scope__intro strong {
  font-size: var(--fs-md);
  color: var(--c-ink);
}

.scope__intro span {
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--c-text-tertiary);
}

.scope__items {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0;
}

.scope__item {
  padding: 0 var(--sp-5);
  border-left: 1px solid var(--c-border);
}

.scope__item dt {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.scope__item dd {
  margin: 4px 0 0;
  font-size: var(--fs-xl);
  font-weight: 700;
  color: var(--c-ink);
  font-variant-numeric: tabular-nums;
}

.scope__item dd small {
  margin-left: 2px;
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--c-text-tertiary);
}

.scope__link {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--fs-sm);
  font-weight: 500;
  white-space: nowrap;
}

/* 作品说明：功能入口 */
.entries__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
}

.entry {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
  padding: var(--sp-5);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  color: var(--c-text);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

.entry:hover {
  text-decoration: none;
  border-color: var(--c-primary-border);
  box-shadow: var(--shadow-md);
}

.entry__icon {
  width: 36px;
  height: 36px;
  border-radius: var(--r-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  margin-bottom: var(--sp-1);
}

.entry__title {
  font-size: var(--fs-md);
  font-weight: 700;
  color: var(--c-ink);
}

.entry__desc {
  flex: 1;
  font-size: var(--fs-sm);
  line-height: 1.6;
  color: var(--c-text-secondary);
}

.entry__go {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-top: var(--sp-2);
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--c-text-tertiary);
  transition: color var(--t-fast);
}

.entry:hover .entry__go {
  color: var(--c-primary);
}

/* 作品说明：边界 */
.bounds__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
}

.bounds__col {
  padding: var(--sp-5) var(--sp-6);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
}

.bounds__col h3 {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-md);
  font-weight: 700;
  color: var(--c-ink);
}

.bounds__badge {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.bounds__col--do .bounds__badge {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.bounds__col--dont .bounds__badge {
  background: var(--c-danger-soft);
  color: var(--c-danger);
}

.bounds__col ul {
  margin: var(--sp-3) 0 0;
  padding-left: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  line-height: 1.6;
  color: var(--c-text-secondary);
}

@media (max-width: 1080px) {
  .hero {
    grid-template-columns: 1fr;
    gap: var(--sp-6);
  }

  .scope {
    grid-template-columns: 1fr;
    gap: var(--sp-4);
  }

  .scope__item:first-child {
    padding-left: 0;
    border-left: none;
  }

  .entries__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .hero {
    padding-top: 0;
  }

  .hero__title {
    font-size: 30px;
  }

  .hero__subtitle {
    font-size: var(--fs-md);
  }

  .chain {
    padding: var(--sp-5);
  }

  .scope {
    padding: var(--sp-4);
  }

  .scope__items {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: var(--sp-4);
  }

  .scope__item {
    padding: 0 var(--sp-3);
  }

  .scope__item:nth-child(odd) {
    padding-left: 0;
    border-left: none;
  }

  .entries__grid,
  .bounds__grid {
    grid-template-columns: 1fr;
  }
}
</style>
