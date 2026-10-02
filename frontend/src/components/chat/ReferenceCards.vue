<script setup lang="ts">
import type { ReferenceItem } from "@/types/api";
import { IconDocument, IconExpand } from "@/components/ui/icons";
import RegisteredAsset from "./RegisteredAsset.vue";

defineProps<{
  references: Array<Partial<ReferenceItem>>;
  compact?: boolean;
  live?: boolean;
}>();

const emit = defineEmits<{ openDetail: [] }>();

function shortName(path?: string | null): string {
  const raw = String(path ?? "");
  const segments = raw.split(/[\\/]/).filter(Boolean);
  return segments[segments.length - 1] ?? raw;
}
</script>

<template>
  <div class="refs">
    <div class="refs__head">
      <span class="refs__title">引用来源（{{ references.length }}）</span>
      <button v-if="compact" class="refs__more" type="button" @click="emit('openDetail')">
        <IconExpand :size="13" />
        查看详情
      </button>
    </div>
    <ul class="refs__list">
      <li v-for="(item, idx) in compact ? references.slice(0, 3) : references" :key="idx" class="refs__item">
        <span class="refs__index">{{ idx + 1 }}</span>
        <div class="refs__body">
          <div class="refs__source">
            <IconDocument :size="13" />
            <span>{{ item.source_title || shortName(item.paper_path) }}</span>
          </div>
          <p class="refs__text" :class="{ 'refs__text--clamp': compact }">{{ item.text }}</p>
          <p v-if="item.page_start || item.section_title" class="refs__text">
            {{ item.page_start ? `第 ${item.page_start}${item.page_end && item.page_end !== item.page_start ? `–${item.page_end}` : ''} 页` : '' }}
            {{ item.section_title }}
          </p>
          <RegisteredAsset :asset-id="item.asset_id" :url="item.asset_url || item.source_url || item.paper_path" :page="item.page_start" :disabled="live" label="查看引用原文" />
          <small v-if="!compact && item.document_version">文档版本 {{ item.document_version }}</small>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.refs {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  background: var(--c-surface);
  padding: var(--sp-3);
}

.refs__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp-2);
}

.refs__title {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-text);
}

.refs__more {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: var(--fs-xs);
  color: var(--c-primary);
}

.refs__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.refs__item {
  display: flex;
  gap: var(--sp-2);
}

.refs__index {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}

.refs__body {
  min-width: 0;
}

.refs__source {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--c-text-secondary);
  overflow-wrap: anywhere;
}

.refs__text {
  margin-top: 2px;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--c-text-tertiary);
  overflow-wrap: anywhere;
}

.refs__text--clamp {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
