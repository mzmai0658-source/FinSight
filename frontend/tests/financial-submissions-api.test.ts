import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../src/services/api";

interface RequestRecord { method: string; url: string; headers: Record<string, string>; body: unknown; responseType: string }
const requests: RequestRecord[] = [];
const responses: unknown[] = [];
const responseStatuses: number[] = [];

beforeEach(() => {
  class FakeXhr {
    onloadend: (() => void) | null = null;
    status = 200;
    statusText = "OK";
    response: unknown = "";
    responseText = "";
    responseType = "";
    timeout = 0;
    headers: Record<string, string> = {};
    method = "";
    url = "";
    open(method: string, url: string) { this.method = method; this.url = url; }
    setRequestHeader(key: string, value: string) { this.headers[key] = value; }
    getAllResponseHeaders() { return "content-type: application/json\r\n"; }
    send(body: unknown) {
      this.status = responseStatuses.shift() ?? 200;
      const next = responses.shift();
      this.response = next instanceof Blob ? next : JSON.stringify(next);
      this.responseText = typeof this.response === "string" ? this.response : "";
      requests.push({ method: this.method, url: this.url, headers: this.headers, body, responseType: this.responseType });
      queueMicrotask(() => this.onloadend?.());
    }
    abort() {}
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  api.saveStoredAuth({ accessToken: "test-access", refreshToken: "test-refresh", user: { id: 7, username: "member", nickname: "", role: "USER", riskProfile: "balanced" } });
});
afterEach(() => { requests.length = 0; responses.length = 0; responseStatuses.length = 0; localStorage.clear(); vi.unstubAllGlobals(); });
const ok = (data: unknown) => ({ code: 0, data, message: "ok" });

it("submits a PDF and restores only the owner's paged server records", async () => {
  expect(api.submitFinancialSubmission).toBeTypeOf("function");
  const record = { id: 31, status: "PENDING", reviewStatus: "PROCESSING" };
  responses.push(ok(record), ok({ records: [record], total: 1, page: 2 }), ok(record), ok(record));
  const file = new File(["%PDF-1.7"], "2025.pdf", { type: "application/pdf" });
  expect(await api.submitFinancialSubmission(file)).toEqual(record);
  expect(await api.fetchFinancialSubmissions({ page: 2, size: 20 })).toMatchObject({ total: 1 });
  expect(await api.fetchFinancialSubmission(31)).toEqual(record);
  await api.retryFinancialSubmission(31);
  expect(requests.map(request => [request.method, request.url])).toEqual([
    ["POST", "/api/financial-submissions"],
    ["GET", "/api/financial-submissions?page=2&size=20"],
    ["GET", "/api/financial-submissions/31"],
    ["POST", "/api/financial-submissions/31/retry"],
  ]);
  expect((requests[0].body as FormData).get("file")).toBe(file);
  expect(requests.every(request => request.headers.Authorization === "Bearer test-access")).toBe(true);
});

it("sends exact review identity and only selected ids to the Java admin API", async () => {
  expect(api.reviewFinancialSubmission).toBeTypeOf("function");
  responses.push(ok({ records: [], total: 0, page: 1 }), ok({ candidate_id: "sha" }), ok({ id: 31 }), ok({ version: "v2", reports: 2, facts: 8, collection: "candidate-v2" }));
  await api.fetchAdminFinancialSubmissions({ page: 1, size: 20, reviewStatus: "WAITING" });
  await api.fetchFinancialSubmissionCandidate(31);
  const review = { decision: "approve" as const, reason: "原件已核对", identity: { stock_code: "000333", company: "美的集团", year: 2025, period: "FY", aliases: ["美的"] } };
  await api.reviewFinancialSubmission(31, review);
  expect(await api.publishFinancialSubmissions([31, 42])).toMatchObject({ version: "v2" });
  expect(requests.map(request => request.url)).toEqual([
    "/api/admin/financial-submissions?page=1&size=20&reviewStatus=WAITING",
    "/api/admin/financial-submissions/31/candidate",
    "/api/admin/financial-submissions/31/review",
    "/api/admin/financial-submissions/publish",
  ]);
  expect(JSON.parse(requests[2].body as string)).toEqual(review);
  expect(JSON.parse(requests[3].body as string)).toEqual({ ids: [31, 42] });
});

it("loads an unpublished original with bearer headers and a PDF blob response", async () => {
  expect(api.fetchFinancialSubmissionOriginal).toBeTypeOf("function");
  const original = new Blob(["%PDF-1.7"], { type: "application/pdf" });
  responses.push(original);
  expect(await api.fetchFinancialSubmissionOriginal(31)).toBe(original);
  expect(requests[0]).toMatchObject({ method: "GET", url: "/api/financial-submissions/31/original", responseType: "blob", headers: { Authorization: "Bearer test-access" } });
  expect(requests[0].url).not.toContain("test-access");
});

it("rejects business failures and non-PDF original content", async () => {
  expect(api.publishFinancialSubmissions).toBeTypeOf("function");
  responses.push({ code: 409, message: "候选尚未批准", data: null }, new Blob(["<html>login</html>"], { type: "text/html" }));
  await expect(api.publishFinancialSubmissions([31])).rejects.toThrow("候选尚未批准");
  await expect(api.fetchFinancialSubmissionOriginal(31)).rejects.toThrow("PDF");
});

it("retains the ApiResponse reason when protected original loading fails with JSON in a blob", async () => {
  responses.push(new Blob([JSON.stringify({ code: 403, message: "无权读取此提交原件", data: null })], { type: "application/json" }));
  responseStatuses.push(403);
  await expect(api.fetchFinancialSubmissionOriginal(31)).rejects.toThrow("无权读取此提交原件");
});
