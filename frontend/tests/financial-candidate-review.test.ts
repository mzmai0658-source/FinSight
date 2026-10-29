import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApp, h, reactive } from "vue";
import CandidateReviewPanel from "../src/components/submission/CandidateReviewPanel.vue";

const api = vi.hoisted(() => ({ fetchFinancialSubmissionCandidate: vi.fn(), reviewFinancialSubmission: vi.fn() }));
vi.mock("../src/services/api", () => api);
const submission = { id: 31, fileName: "原始财报.pdf", status: "SUCCESS", reviewStatus: "WAITING", candidateId: "sha", stockCode: "000333", company: "美的集团", reportYear: 2025, reportPeriod: "FY" };
const candidate = () => ({
  candidate_id: "sha", page_count: 80, identity: { stock_code: "000333", company: "美的集团", year: 2025, period: "FY", aliases: ["美的"] },
  quality: { identity_verified: true, facts_verified: true, reports_verified: true, index_verified: true, verified_count: 1, excluded_count: 1, conflicts: [] },
  facts: [{ id: "fact-1", metric: "revenue", value: "0", unit: "元", year: 2025, period: "FY", scope: "consolidated", status: "unverified", source: { page: 7, table: "合并利润表", row: "营业收入", column: "本期金额", raw_value: "0.00", raw_unit: "元", literal: "营业收入 0.00", verification: "unverified", original_cell: { proof: "待核验" } } }],
  excluded: [{ metric: "net_profit", reason: "未披露", status: "not_disclosed" }],
});
let app: ReturnType<typeof createApp> | undefined;
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
beforeEach(() => { api.fetchFinancialSubmissionCandidate.mockResolvedValue(candidate()); });
afterEach(() => { app?.unmount(); document.body.innerHTML = ""; vi.resetAllMocks(); });
async function mount(originalOpened = true) {
  const root = document.createElement("div"); document.body.append(root);
  const changed = vi.fn();
  const props = reactive({ submission: { ...submission } as any, originalOpened });
  app = createApp({ render: () => h(CandidateReviewPanel, { ...props, onChanged: changed }) }); app.mount(root); await settle();
  return { root, changed, update: () => { props.submission = { ...submission }; } };
}
const button = (root: Element, text: string) => [...root.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.trim() === text)!;
function input(root: Element, label: string, value: string) {
  const element = root.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
  element.value = value; element.dispatchEvent(new Event("input", { bubbles: true }));
}

it("renders nested v3 source evidence, zero values and exclusions without implying verification", async () => {
  const { root } = await mount();
  expect(root.textContent).toContain("合并利润表");
  expect(root.textContent).toContain("0.00");
  expect(root.textContent).toContain("营业收入 0.00");
  expect(root.textContent).toContain("尚未核验");
  expect(root.textContent).toContain("未披露");
  expect(root.textContent).toContain("查看第 7 页");
});

it("requires manual identity confirmation after opening the original and sends canonical identity", async () => {
  const { root, changed } = await mount();
  expect(button(root, "批准，等待发布").disabled).toBe(true);
  input(root, "公司别名", "美的， Midea; 美的");
  root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!.click(); await settle();
  api.reviewFinancialSubmission.mockResolvedValue({ ...submission, reviewStatus: "APPROVED" });
  button(root, "批准，等待发布").click(); await settle();
  expect(api.reviewFinancialSubmission).toHaveBeenCalledWith(31, { decision: "approve", reason: "", identity: { stock_code: "000333", company: "美的集团", year: 2025, period: "FY", aliases: ["美的", "Midea"] } });
  expect(changed).toHaveBeenCalledWith(expect.objectContaining({ reviewStatus: "APPROVED" }));
});

it("does not allow confirmation before loading the original PDF", async () => {
  const { root } = await mount(false);
  expect(root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!.disabled).toBe(true);
  expect(button(root, "批准，等待发布").disabled).toBe(true);
});

it("requires confirmation again if the reviewer changes the report identity", async () => {
  const { root } = await mount();
  const confirmation = root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!;
  confirmation.click(); await settle();
  expect(button(root, "批准，等待发布").disabled).toBe(false);
  input(root, "公司名称", "待重新核对的公司"); await settle();
  expect(confirmation.checked).toBe(false);
  expect(button(root, "批准，等待发布").disabled).toBe(true);
});

it("preserves the review draft and confirmation when polling returns the same report state", async () => {
  const { root, update } = await mount();
  input(root, "审核原因", "正在逐项核对");
  input(root, "公司别名", "美的, Midea");
  const confirmation = root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!;
  confirmation.click(); await settle();
  update(); await settle();
  expect(api.fetchFinancialSubmissionCandidate).toHaveBeenCalledOnce();
  expect(root.querySelector<HTMLTextAreaElement>('[aria-label="审核原因"]')!.value).toBe("正在逐项核对");
  expect(root.querySelector<HTMLInputElement>('[aria-label="公司别名"]')!.value).toBe("美的, Midea");
  expect(confirmation.checked).toBe(true);
  expect(button(root, "批准，等待发布").disabled).toBe(false);
});

it("requires a rejection reason and preserves a server review error", async () => {
  const { root, changed } = await mount();
  expect(button(root, "拒绝").disabled).toBe(true);
  input(root, "审核原因", "原件报告期冲突"); await settle();
  api.reviewFinancialSubmission.mockRejectedValue(new Error("候选版本已变化，请重新核对"));
  button(root, "拒绝").click(); await settle();
  expect(api.reviewFinancialSubmission).toHaveBeenCalledWith(31, { decision: "reject", reason: "原件报告期冲突" });
  expect(root.querySelector('[role="alert"]')?.textContent).toContain("候选版本已变化");
  expect(changed).not.toHaveBeenCalled();
});

it("blocks approval when known conflicts remain and clearly marks missing source data", async () => {
  const item = candidate();
  item.quality.conflicts = ["report_period_mismatch"] as any;
  item.facts = [{ metric: "revenue", value: "0", status: "unverified" }] as any;
  api.fetchFinancialSubmissionCandidate.mockResolvedValue(item);
  const { root } = await mount();
  root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!.click(); await settle();
  expect(button(root, "批准，等待发布").disabled).toBe(true);
  expect(root.textContent).toContain("存在未解决冲突");
  expect(root.textContent).toContain("来源位置未登记");
});

it("keeps an extraction gap distinct from a report explicitly marked not disclosed", async () => {
  const item = candidate();
  item.excluded = {
    missing: [{ metric: "eps_basic", reason: "column_not_identified" }],
    not_disclosed: [{ metric: "roe_weighted", reason: "explicit_original_absence" }],
  } as any;
  api.fetchFinancialSubmissionCandidate.mockResolvedValue(item);
  const { root } = await mount();
  const entries = [...root.querySelectorAll('[aria-label="已排除的数据"] li')];
  expect(entries[0].textContent).toContain("未提取，原因待核查");
  expect(entries[0].textContent).not.toContain("未披露");
  expect(entries[1].textContent).toContain("未披露");
});

it("blocks approval when the server has not supplied all quality checks", async () => {
  const item = candidate();
  item.quality = { verified_count: 1 } as any;
  api.fetchFinancialSubmissionCandidate.mockResolvedValue(item);
  const { root } = await mount();
  root.querySelector<HTMLInputElement>('[aria-label="确认已人工核对原件身份"]')!.click();
  await settle();
  expect(button(root, "批准，等待发布").disabled).toBe(true);
  expect(root.textContent).toContain("质量核验尚未完成");
  button(root, "批准，等待发布").click();
  await settle();
  expect(api.reviewFinancialSubmission).not.toHaveBeenCalled();
});
