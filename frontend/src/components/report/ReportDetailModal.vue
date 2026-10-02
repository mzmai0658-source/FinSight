<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import type { ResearchReportItem } from "@/types/platform";
import ResearchReader from './ResearchReader.vue';
import AppDialog from '@/components/ui/AppDialog.vue';

const props = defineProps<{
  report: ResearchReportItem | null;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const router = useRouter();

const visible = computed(() => props.report !== null);
const isStock = computed(() => props.report?.reportType === "stock");
const isBuyRating = computed(() => props.report?.rating?.includes("买") ?? false);

function close() {
  emit("close");
}

function goToCompany() {
  if (props.report?.stockCode) {
    router.push(`/market/${props.report.stockCode}`);
    close();
  }
}

</script>

<template>
  <AppDialog :open="visible" :label="report?.title || '研报详情'" @close="close">
    <div class="modal-panel">
      <header class="modal-header">
        <div class="modal-tags">
          <span
            class="modal-type"
            :class="{ 'modal-type--industry': !isStock }"
          >
            {{ isStock ? "个股研报" : "行业研报" }}
          </span>
          <span v-if="report?.rating" class="modal-rating" :class="{ 'modal-rating--buy': isBuyRating }">
            {{ report.rating }}
          </span>
        </div>
        <button class="modal-close" type="button" aria-label="关闭" @click="close">×</button>
      </header>

      <div class="modal-body">
        <h2 class="modal-title">{{ report?.title }}</h2>

        <div class="modal-meta-grid">
          <div class="meta-item">
            <span class="meta-label">{{ isStock ? "公司" : "行业" }}</span>
            <span class="meta-value">
              <template v-if="isStock">
                {{ report?.stockName || "—" }}
                <span v-if="report?.stockCode" class="meta-code mono">{{ report.stockCode }}</span>
              </template>
              <template v-else>
                {{ report?.industryName || "—" }}
              </template>
            </span>
          </div>

          <div class="meta-item">
            <span class="meta-label">发布机构</span>
            <span class="meta-value">{{ report?.orgSname || report?.orgName || "—" }}</span>
          </div>

          <div v-if="report?.researcher" class="meta-item">
            <span class="meta-label">分析师</span>
            <span class="meta-value">{{ report.researcher }}</span>
          </div>

          <div class="meta-item">
            <span class="meta-label">发布日期</span>
            <span class="meta-value mono">{{ report?.publishDate || "—" }}</span>
          </div>

          <div v-if="report?.predictThisYearEps" class="meta-item">
            <span class="meta-label">预测 EPS</span>
            <span class="meta-value mono">{{ report.predictThisYearEps }}</span>
          </div>

          <div v-if="report?.predictThisYearPe" class="meta-item">
            <span class="meta-label">预测 PE</span>
            <span class="meta-value mono">{{ report.predictThisYearPe }}</span>
          </div>

          <div v-if="report?.aimPrice" class="meta-item">
            <span class="meta-label">目标价</span>
            <span class="meta-value mono">{{ report.aimPrice }}</span>
          </div>

          <div v-if="report?.lastRating" class="meta-item">
            <span class="meta-label">上期评级</span>
            <span class="meta-value">{{ report.lastRating }}</span>
          </div>
        </div>

        <div v-if="report?.pdfPresent" class="modal-notice">
          <span class="notice-icon">✓</span>
          <span>本地 PDF 已收录，可在下方阅读已有 OCR 正文。</span>
        </div>

        <ResearchReader v-if="report?.pdfPresent" :key="report.id" :report-id="report.id" />

        <div class="modal-actions">
          <button
            v-if="report?.stockCode"
            type="button"
            class="btn btn--primary"
            @click="goToCompany"
          >
            查看公司详情
          </button>
          <button type="button" class="btn btn--secondary" @click="close">
            关闭
          </button>
        </div>
      </div>
    </div>
  </AppDialog>
</template>

<style scoped>
.modal-panel {
  width: 100%;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-lg, 0 12px 40px rgb(0 0 0 / 14%));
}

.modal-header {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--c-surface);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--c-border);
}

.modal-tags {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.modal-type {
  font-size: var(--fs-xs);
  font-weight: 600;
  border-radius: var(--r-full);
  padding: 3px 10px;
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.modal-type--industry {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.modal-rating {
  font-size: var(--fs-xs);
  font-weight: 600;
  border-radius: var(--r-full);
  padding: 3px 10px;
  background: var(--c-bg);
  color: var(--c-text-secondary);
}

.modal-rating--buy {
  background: var(--c-danger-soft);
  color: var(--c-danger);
}

.modal-close {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--c-text-secondary);
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
  border-radius: var(--r-full);
  transition: background var(--t-fast), color var(--t-fast);
}

.modal-close:hover {
  background: var(--c-bg);
  color: var(--c-text);
}

.modal-body {
  padding: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.modal-title {
  font-size: var(--fs-lg);
  font-weight: 700;
  line-height: 1.45;
  color: var(--c-text);
}

.modal-meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--sp-3);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.meta-value {
  font-size: var(--fs-sm);
  color: var(--c-text);
  font-weight: 500;
}

.meta-code {
  margin-left: var(--sp-1);
  color: var(--c-text-tertiary);
  font-weight: 400;
}

.modal-notice {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border-radius: var(--r-lg);
  background: var(--c-success-soft);
  color: var(--c-success);
  font-size: var(--fs-sm);
}

.notice-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--c-success);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.modal-actions {
  display: flex;
  gap: var(--sp-3);
  justify-content: flex-end;
  padding-top: var(--sp-2);
}

.btn {
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  color: var(--c-text);
  padding: 9px var(--sp-5);
  border-radius: var(--r-full);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast);
}

.btn--primary {
  background: var(--c-primary);
  border-color: var(--c-primary);
  color: #fff;
}

.btn--primary:hover {
  opacity: 0.92;
}

.btn--secondary:hover {
  background: var(--c-bg);
}

@media (max-width: 640px) {
  .modal-meta-grid {
    grid-template-columns: 1fr;
  }

  .modal-actions {
    flex-direction: column-reverse;
  }

  .btn {
    width: 100%;
    text-align: center;
  }
}
</style>
