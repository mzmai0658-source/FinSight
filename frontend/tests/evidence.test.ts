import { afterEach, describe, expect, it } from "vitest";
import { createApp, nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import EvidenceRail from "../src/components/EvidenceRail.vue";
import ReferenceCards from "../src/components/chat/ReferenceCards.vue";
import ChatMessage from "../src/components/chat/ChatMessage.vue";
import { viewFromMessage, viewFromStreaming } from "../src/components/chat/messageView";
import { useUiStore } from "../src/stores/ui";
import type { AssistantMetadata, SessionMessage } from "../src/types/api";

const evidence = [
  { id: "query-1", type: "sql" as const, sql: "SELECT revenue FROM a", source: {}, rows: [{ revenue: 101 }], columns: ["revenue"], row_count: 1 },
  { id: "query-2", type: "sql" as const, sql: "SELECT profit FROM b", source: {}, rows: [{ profit: 202 }], columns: ["profit"], row_count: 1 },
];
const verification = { status: "pass" as const, checks: [], unmatched_numbers: [], scope: "仅核对结构化财务事实" };
const facts = [{ fact_id: "f1", query_id: "query-1", row_id: "r1", field: "revenue", value: 101, unit: "万元" }];
const base = { sql: "SELECT revenue FROM a;\nSELECT profit FROM b", references: [], images: [], execution_plan: [], chart_format: "无", context: {}, needs_clarification: false };
const message = (metadata: AssistantMetadata): SessionMessage => ({ role: "assistant", content: "答案", ts: "", metadata });
const apps: ReturnType<typeof createApp>[] = [];
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML = ""; localStorage.clear(); });

describe("Evidence round trip and display", () => {
  it("keeps help responses distinct from financial verification after loading history", () => {
    const pinia = createPinia(); setActivePinia(pinia);
    const view = viewFromMessage(message({ ...base, response_kind: "conversation", verification, validation: {}, outcome: { status: "answered", reason_codes: [] } }), 1);
    const el = document.createElement("div"); document.body.append(el);
    const app = createApp(ChatMessage, { view }); apps.push(app); app.use(pinia).mount(el);
    expect(view.responseKind).toBe("conversation");
    expect(el.textContent).toContain("已完成");
    expect(el.textContent).not.toContain("核验通过");
  });
  it("shows query failure independently of verification and preserves legacy absence", () => {
    const pinia = createPinia(); setActivePinia(pinia);
    const view = viewFromMessage(message({ ...base, verification, validation: {}, outcome: { status: "query_failed", reason_codes: ["upstream_failure"] } }), 1);
    const el = document.createElement("div"); document.body.append(el);
    const app = createApp(ChatMessage, { view }); apps.push(app); app.use(pinia).mount(el);
    expect(el.querySelector('[role="status"]')?.textContent).toBe("本轮查询失败");
    expect(el.textContent).not.toContain("核验通过");
    expect(viewFromMessage(message({ ...base, validation: {} }), 1).outcome).toBeUndefined();
  });
  it("keeps both query result sets for live and persisted metadata", () => {
    const current = viewFromMessage(message({ ...base, evidence, verification, facts, validation: {} }), 1);
    const historical = viewFromMessage(message({ ...base, validation: { evidence, verification, facts } }), 1);
    expect(current.sqlEvidence).toEqual(historical.sqlEvidence);
    expect(current.sqlEvidence.map((query) => query.rows)).toEqual([[{ revenue: 101 }], [{ profit: 202 }]]);
    expect(current.facts).toEqual(historical.facts);
    expect(current.verification?.scope).toBe("仅核对结构化财务事实");
    const streamed = viewFromStreaming({ question: "q", content: "", charts: [], clarify: null, error: "", steps: evidence.map((e) => ({ tool: "sql", label: "query", detail: "", status: "done" as const, queryId: e.id, sql: e.sql, rows: e.rows })) });
    expect(streamed.assistant.sqlEvidence).toHaveLength(2);
  });
  it("renders each SQL with its own rows and highlights chart-linked query", async () => {
    const pinia = createPinia(); setActivePinia(pinia);
    const ui = useUiStore(); ui.openRail("sql", 1, "query-1");
    const el = document.createElement("div"); document.body.append(el);
    const view = viewFromMessage(message({ ...base, evidence, facts, verification, validation: {} }), 1);
    const app = createApp(EvidenceRail, { view }); apps.push(app); app.use(pinia).mount(el); await nextTick();
    const query1 = el.querySelector("#evidence-query-query-1")!;
    const query2 = el.querySelector("#evidence-query-query-2")!;
    expect(query1.textContent).toContain("101"); expect(query1.textContent).not.toContain("202");
    expect(query2.textContent).toContain("202"); expect(query2.textContent).not.toContain("101");
    expect(query1.classList.contains("rail__sql--focused")).toBe(true);
    expect(query1.querySelector('[data-source-status="source_unlocated"]')?.textContent).toContain("原文位置未登记");
  });
  it("renders original page range and a controlled asset action", () => {
    const el = document.createElement("div"); document.body.append(el);
    const app = createApp(ReferenceCards, { references: [{ paper_path: "local.pdf", source_title: "年报", text: "原文", page_start: 8, page_end: 9, asset_id: "0123456789abcdef" }] });
    apps.push(app); app.mount(el);
    expect(el.textContent).toContain("第 8–9 页");
    expect(el.querySelector(".registered-asset button")?.textContent).toContain("查看引用原文");
    expect(el.querySelector('a[href="local.pdf"]')).toBeNull();
  });
});
