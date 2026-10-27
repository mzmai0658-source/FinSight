import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApp } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import FinancialSubmissionsPage from "../src/pages/FinancialSubmissionsPage.vue";

const api = vi.hoisted(() => ({
  fetchUnreadCount: vi.fn().mockResolvedValue(0), fetchFinancialSubmissions: vi.fn(), fetchAdminFinancialSubmissions: vi.fn(),
  fetchFinancialSubmission: vi.fn(), submitFinancialSubmission: vi.fn(), retryFinancialSubmission: vi.fn(),
  fetchFinancialSubmissionOriginal: vi.fn(), fetchFinancialSubmissionCandidate: vi.fn(), reviewFinancialSubmission: vi.fn(), publishFinancialSubmissions: vi.fn(),
}));
vi.mock("../src/services/api", () => api);
vi.mock("../src/stores/auth", () => ({ useAuthStore: () => ({ user: { nickname: "研究用户", username: "user", role: "ADMIN" }, isAuthenticated: true }) }));
const record = (id = 31, reviewStatus = "WAITING", status = "SUCCESS") => ({
  id, taskUid: `task-${id}`, fileName: `财报-${id}.pdf`, status, reviewStatus, candidateId: "sha", sourceSha256: "abc",
  stockCode: "000333", company: "美的集团", reportYear: 2025, reportPeriod: "FY", message: "处理完成，等待审核", createdAt: "2026-10-21T00:00:00", publishedVersion: null,
});
let app: ReturnType<typeof createApp> | undefined;
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
beforeEach(() => {
  api.fetchFinancialSubmissions.mockResolvedValue({ records: [record()], total: 1, page: 1 });
  api.fetchAdminFinancialSubmissions.mockResolvedValue({ records: [], total: 0, page: 1 });
  api.fetchFinancialSubmission.mockResolvedValue(record());
  api.fetchFinancialSubmissionCandidate.mockResolvedValue({ candidate_id: "sha", identity: {}, facts: [], excluded: [] });
  api.fetchFinancialSubmissionOriginal.mockResolvedValue(new Blob(["%PDF"], { type: "application/pdf" }));
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:test-original"), revokeObjectURL: vi.fn() }));
});
afterEach(() => { app?.unmount(); document.body.innerHTML = ""; vi.resetAllMocks(); vi.unstubAllGlobals(); });
async function mount(url = "/financial-submissions", admin = false) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/:pathMatch(.*)*", component: { render: () => null } }] });
  await router.push(url);
  const root = document.createElement("div"); document.body.append(root);
  app = createApp(FinancialSubmissionsPage, { admin }); app.use(router); app.mount(root); await settle(); await settle();
  return { root, router };
}
const button = (root: Element, text: string) => [...root.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.trim() === text)!;

it("restores selected server detail from the URL and separates processing from review", async () => {
  const { root } = await mount("/financial-submissions?id=31");
  expect(api.fetchFinancialSubmission).toHaveBeenCalledWith(31);
  expect(root.querySelector('[aria-label="提交详情"]')?.textContent).toContain("处理完成");
  expect(root.querySelector('[aria-label="提交详情"] [data-testid="review-status"]')?.textContent).toBe("待审核");
  button(root, "查看原始 PDF").click(); await settle();
  expect(api.fetchFinancialSubmissionOriginal).toHaveBeenCalledWith(31);
  expect(root.querySelector("iframe")?.getAttribute("src")).toContain("blob:test-original");
  app!.unmount(); app = undefined;
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-original");
});

it("waits for server acceptance before showing upload success", async () => {
  let accept!: (value: unknown) => void;
  api.submitFinancialSubmission.mockReturnValue(new Promise(resolve => { accept = resolve; }));
  const { root } = await mount();
  const input = root.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["%PDF"], "新财报.pdf", { type: "application/pdf" })] });
  input.dispatchEvent(new Event("change", { bubbles: true })); await settle();
  button(root, "提交 PDF").click(); await settle();
  expect(root.textContent).toContain("正在上传");
  expect(root.querySelector('[role="status"]')?.textContent || "").not.toContain("已接收");
  accept(record(32, "PROCESSING", "PENDING")); await settle(); await settle();
  expect(root.querySelector('[role="status"]')?.textContent).toContain("已接收");
  expect(api.submitFinancialSubmission).toHaveBeenCalledOnce();
});

it("keeps the last server records on refresh failure and allows recovery", async () => {
  const { root } = await mount();
  api.fetchFinancialSubmissions.mockRejectedValueOnce(new Error("服务暂时不可用"));
  button(root, "刷新记录").click(); await settle();
  expect(root.textContent).toContain("服务暂时不可用");
  expect(root.textContent).toContain("财报-31.pdf");
  expect(root.textContent).not.toContain("还没有提交记录");
  button(root, "刷新记录").click(); await settle();
  expect(root.textContent).not.toContain("服务暂时不可用");
});

it("rejects a non-PDF without sending it to the server", async () => {
  const { root } = await mount();
  const input = root.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["text"], "财报.txt", { type: "text/plain" })] });
  input.dispatchEvent(new Event("change", { bubbles: true })); await settle();
  expect(root.querySelector('[role="alert"]')?.textContent).toContain("PDF");
  expect(button(root, "提交 PDF").disabled).toBe(true);
  expect(api.submitFinancialSubmission).not.toHaveBeenCalled();
});

it("publishes only checked approved records and retains the choice after a failed receipt", async () => {
  api.fetchAdminFinancialSubmissions.mockResolvedValue({ records: [record(31, "APPROVED"), record(32, "WAITING"), record(33, "APPROVED")], total: 3, page: 1 });
  api.publishFinancialSubmissions.mockRejectedValueOnce(new Error("索引发布失败"));
  const { root } = await mount("/admin/financial-submissions?reviewStatus=APPROVED", true);
  const checks = root.querySelectorAll<HTMLInputElement>('input[aria-label^="选择发布"]');
  expect(checks).toHaveLength(3);
  expect(checks[1].disabled).toBe(true);
  checks[0].click(); await settle();
  button(root, "发布选中财报（1）").click(); await settle();
  expect(api.publishFinancialSubmissions).toHaveBeenCalledWith([31]);
  expect(root.textContent).toContain("索引发布失败");
  expect(checks[0].checked).toBe(true);
  expect(root.textContent).not.toContain("发布版本：");
  api.publishFinancialSubmissions.mockResolvedValueOnce({ version: "v2", reports: 1, facts: 12, collection: "published-v2" });
  button(root, "发布选中财报（1）").click(); await settle();
  expect(root.textContent).toContain("发布版本：v2");
});

it("loads paged records from the server and carries the page in the URL", async () => {
  api.fetchFinancialSubmissions.mockResolvedValue({ records: [record()], total: 21, page: 1 });
  const { root, router } = await mount();
  button(root, "下一页").click(); await settle(); await settle();
  expect(api.fetchFinancialSubmissions).toHaveBeenLastCalledWith({ page: 2, size: 20 });
  expect(router.currentRoute.value.query.page).toBe("2");
});

it("keeps the opened original and manual confirmation during an unchanged admin detail refresh", async () => {
  api.fetchAdminFinancialSubmissions.mockResolvedValue({ records: [record()], total: 1, page: 1 });
  api.fetchFinancialSubmissionCandidate.mockResolvedValue({
    candidate_id: "sha", identity: { stock_code: "000333", company: "美的集团", year: 2025, period: "FY", aliases: [] },
    quality: { verified_count: 1 }, facts: [], excluded: [],
  });
  api.fetchFinancialSubmission.mockImplementation(async () => ({ ...record() }));
  const { root } = await mount("/admin/financial-submissions?id=31", true);
  button(root, "查看原始 PDF").click(); await settle();
  const confirmation = root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!;
  confirmation.click(); await settle();
  button(root, "刷新记录").click(); await settle(); await settle();
  expect(root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!.disabled).toBe(false);
  expect(root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!.checked).toBe(true);
  expect(root.querySelector("iframe")?.getAttribute("src")).toContain("blob:test-original");
});

it("requires a fresh PDF load if the server source digest changes", async () => {
  const { root } = await mount("/financial-submissions?id=31");
  button(root, "查看原始 PDF").click(); await settle();
  api.fetchFinancialSubmission.mockResolvedValue({ ...record(), sourceSha256: "new-digest" });
  button(root, "刷新记录").click(); await settle(); await settle();
  expect(root.querySelector("iframe")).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-original");
  button(root, "查看原始 PDF").click(); await settle();
  expect(api.fetchFinancialSubmissionOriginal).toHaveBeenCalledTimes(2);
});
