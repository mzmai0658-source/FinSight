import { afterEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import { createPinia } from "pinia";
import ChatMessage from "../src/components/chat/ChatMessage.vue";
import { viewFromMessage } from "../src/components/chat/messageView";
import type { EvidenceFact } from "../src/types/api";

const api = vi.hoisted(() => ({ fetchRegisteredAsset: vi.fn(), fetchFinancialPdf: vi.fn() }));
vi.mock("../src/services/api", async (original) => ({ ...await original<typeof import("../src/services/api")>(), ...api }));
let app: ReturnType<typeof createApp> | undefined;
afterEach(() => { app?.unmount(); app = undefined; document.body.innerHTML = ""; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

function fact(year: number, assetId?: string, page = 10): EvidenceFact {
  return { fact_id: `fact-${year}-${assetId}`, query_id: "q1", row_id: `r-${year}`, field: "revenue", value: 100,
    stock_code: "600001", stock_abbr: "示例公司", report_year: year, report_period: "FY",
    source: { page_start: page, page_end: page, ...(assetId ? { asset_id: assetId, source_url: `/api/assets/${assetId}` } : {}) } };
}
async function mount(facts: EvidenceFact[]) {
  api.fetchRegisteredAsset.mockResolvedValue(new Blob(["%PDF-1.7"], { type: "application/pdf" }));
  api.fetchFinancialPdf.mockResolvedValue(new Blob(["%PDF-1.7"], { type: "application/pdf" }));
  vi.spyOn(window, "open").mockReturnValue(null);
  vi.stubGlobal("URL", class extends URL { static createObjectURL = vi.fn(() => "blob:original"); static revokeObjectURL = vi.fn(); });
  const view = viewFromMessage({ role: "assistant", content: "回答", ts: "", metadata: { facts } }, 0);
  const root = document.createElement("div"); document.body.append(root);
  app = createApp(ChatMessage, { view }); app.use(createPinia()); app.mount(root); await nextTick();
  return root;
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("historical fact original shortcuts", () => {
  it("opens each report's saved asset and page instead of the current publication catalogue", async () => {
    const ids = ["original_2023_abcdef", "original_2024_abcdef", "original_2025_abcdef"];
    const root = await mount(ids.map((id, index) => fact(2023 + index, id, 10 + index)));
    const buttons = [...root.querySelectorAll<HTMLButtonElement>(".msg__sources button")];
    for (const button of buttons) { button.click(); await settle(); }
    expect(api.fetchRegisteredAsset.mock.calls.map(([id]) => id)).toEqual(ids);
    expect(api.fetchFinancialPdf).not.toHaveBeenCalled();
    expect([...root.querySelectorAll<HTMLAnchorElement>(".msg__sources a")].map((link) => link.getAttribute("href")))
      .toEqual(["blob:original#page=10", "blob:original#page=11", "blob:original#page=12"]);
  });

  it("uses a registered source URL when an older saved source lacks asset_id", async () => {
    const source = fact(2024, "saved_source_abcdef"); delete source.source!.asset_id;
    const root = await mount([source]); root.querySelector<HTMLButtonElement>(".msg__sources button")!.click(); await settle();
    expect(api.fetchRegisteredAsset).toHaveBeenCalledWith("saved_source_abcdef");
    expect(api.fetchFinancialPdf).not.toHaveBeenCalled();
  });

  it("retains distinct original versions of the same report and page", async () => {
    const root = await mount([fact(2024, "first_version_abcdef"), fact(2024, "second_version_abcdef")]);
    expect(root.querySelectorAll(".msg__sources button")).toHaveLength(2);
  });

  it("keeps the controlled report lookup for old messages without registered assets", async () => {
    const root = await mount([fact(2024)]); root.querySelector<HTMLButtonElement>(".msg__sources button")!.click(); await settle();
    expect(api.fetchFinancialPdf).toHaveBeenCalledWith(expect.objectContaining({ stockCode: "600001", reportYear: 2024, reportPeriod: "FY" }));
    expect(api.fetchRegisteredAsset).not.toHaveBeenCalled();
  });

  it("does not silently replace an unavailable historical asset with a newer publication", async () => {
    const root = await mount([fact(2024, "missing_original_abcd")]);
    api.fetchRegisteredAsset.mockRejectedValueOnce(new Error("原文资产已不存在，请重新检索"));
    root.querySelector<HTMLButtonElement>(".msg__sources button")!.click(); await settle();
    expect(root.querySelector('[role="alert"]')?.textContent ?? "").toContain("原文资产已不存在");
    expect(api.fetchFinancialPdf).not.toHaveBeenCalled();
  });
});
