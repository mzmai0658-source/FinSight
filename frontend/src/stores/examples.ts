import { defineStore } from "pinia";

import { fetchExamples } from "@/services/api";
import type { ExampleItem } from "@/types/api";

const fallbackExamples: ExampleItem[] = [
  { id: "ex-help", type: "使用说明", question: "如何指定公司、年份、报告期和指标来查询财报？" },
  { id: "ex-source", type: "来源核对", question: "如何核对回答中的数字与原文来源？" },
];

export const useExamplesStore = defineStore("examples", {
  state: () => ({
    examples: [] as ExampleItem[],
    loaded: false,
  }),
  actions: {
    async ensureLoaded() {
      if (this.loaded) return;
      try {
        const data = await fetchExamples();
        this.examples = data.examples?.length ? data.examples : fallbackExamples;
      } catch {
        this.examples = fallbackExamples;
      }
      this.loaded = true;
    },
  },
});
