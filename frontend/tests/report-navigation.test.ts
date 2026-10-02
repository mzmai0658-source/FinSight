import { afterEach, describe, expect, it, vi } from "vitest";
import { createApp, h } from "vue";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import ReportsPage from "../src/pages/ReportsPage.vue";

const api = vi.hoisted(() => ({
  loadStoredAuth: vi.fn(() => ({})),
  fetchUnreadCount: vi.fn().mockResolvedValue(0),
  fetchFinancialMaterials: vi.fn().mockResolvedValue({ total: 0, records: [] }),
  fetchFinancialPdf: vi.fn(), fetchFinancialPage: vi.fn(), fetchResearchPage: vi.fn(), fetchReportDetail: vi.fn(),
  fetchReports: vi.fn().mockResolvedValue({ total: 0, records: [] }),
}));
vi.mock("../src/services/api", () => api);
vi.mock("../src/stores/auth", () => ({ useAuthStore: () => ({ user: null, isAuthenticated: false }) }));
let app: ReturnType<typeof createApp>;
afterEach(() => { app?.unmount(); document.body.innerHTML = ""; vi.clearAllMocks(); });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function mount(path = "/reports?tab=financial&company=600080") {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/reports", component: ReportsPage }, { path: "/:pathMatch(.*)*", component: { render: () => null } }] });
  await router.push(path);
  const root = document.createElement("div"); document.body.append(root);
  app = createApp({ render: () => h(RouterView) }); app.use(router); app.mount(root);
  await settle();
  return { root, router };
}

describe("company to report library navigation", () => {
  it("loads the linked company and follows browser back when switching tabs", async () => {
    const { root, router } = await mount();
    expect(api.fetchFinancialMaterials).toHaveBeenLastCalledWith({ page: 1, size: 12, keyword: "600080" });
    root.querySelectorAll<HTMLButtonElement>(".material-tab")[1].click(); await settle();
    expect(router.currentRoute.value.query.tab).toBe("institution");
    router.back(); await settle();
    expect(root.querySelector(".material-tab--active")?.textContent).toContain("公司财报");
    expect(root.querySelector<HTMLInputElement>(".search input")?.value).toBe("600080");
    expect(api.fetchFinancialMaterials).toHaveBeenCalledTimes(1);
  });

  it("updates a company link while mounted and lets the user clear the filter", async () => {
    const { root, router } = await mount();
    await router.push("/reports?tab=financial&company=000002"); await settle();
    expect(api.fetchFinancialMaterials).toHaveBeenLastCalledWith({ page: 1, size: 12, keyword: "000002" });
    root.querySelector<HTMLButtonElement>(".filter-summary button")!.click(); await settle();
    expect(router.currentRoute.value.query.company).toBeUndefined();
    expect(api.fetchFinancialMaterials).toHaveBeenLastCalledWith({ page: 1, size: 12, keyword: "" });
    expect(api.fetchFinancialMaterials).toHaveBeenCalledTimes(3);
  });

  it("retains research search and view when visiting the financial tab", async () => {
    const { root } = await mount("/reports?tab=institution");
    const search = root.querySelector<HTMLInputElement>(".reports__search")!;
    search.value = "年度研究"; search.dispatchEvent(new Event("input"));
    root.querySelector<HTMLButtonElement>('[aria-label="表格视图"]')!.click(); await settle();
    root.querySelectorAll<HTMLButtonElement>(".material-tab")[0].click(); await settle();
    root.querySelectorAll<HTMLButtonElement>(".material-tab")[1].click(); await settle();
    expect(root.querySelector<HTMLInputElement>(".reports__search")?.value).toBe("年度研究");
    expect(root.querySelector('[aria-label="表格视图"]')?.classList.contains("segmented__btn--active")).toBe(true);
  });
});
