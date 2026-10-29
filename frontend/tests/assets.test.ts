import { afterEach, expect, it, vi } from "vitest";
import { Blob as BrowserBlob } from "node:buffer";
import { fetchFinancialPdf, fetchRegisteredAsset, saveStoredAuth, type FinancialMaterial } from "../src/services/api";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
it("fetches protected documents with bearer identity and retypes unsafe MIME", async () => {
  const requests: Array<{ url: string; headers: Record<string, string> }> = [];
  class FakeXhr {
    onloadend: (() => void) | null = null;
    status = 200;
    statusText = "OK";
    response = new Blob(["safe text"], { type: "text/html" });
    responseType = "";
    timeout = 0;
    headers: Record<string, string> = {};
    url = "";
    open(_method: string, url: string) { this.url = url; }
    setRequestHeader(key: string, value: string) { this.headers[key] = value; }
    getAllResponseHeaders() { return "content-type: text/html\r\n"; }
    send() { requests.push(this); queueMicrotask(() => this.onloadend?.()); }
    abort() {}
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  saveStoredAuth({ accessToken: "fake-access", refreshToken: "fake-refresh", user: { id: 7, username: "student", nickname: "", role: "USER", riskProfile: "balanced" } });
  const blob = await fetchRegisteredAsset("0123456789abcdef");
  expect(requests[0].url).toBe("/api/assets/0123456789abcdef");
  expect(requests[0].headers.Authorization).toBe("Bearer fake-access");
  expect(blob.type).toBe("text/plain");
});

it.each(["registered", "catalogue"])("preserves the server's access error for a %s PDF blob request", async (entry) => {
  vi.stubGlobal("Blob", BrowserBlob);
  class ErrorXhr {
    onloadend: (() => void) | null = null;
    status = 404; statusText = "Not Found"; responseType = ""; timeout = 0;
    response = new Blob([JSON.stringify({ code: 40400, message: "原文资产已不存在，请重新检索", data: null })], { type: "application/json" });
    open() {} setRequestHeader() {} abort() {}
    getAllResponseHeaders() { return "content-type: application/json\r\n"; }
    send() { queueMicrotask(() => this.onloadend?.()); }
  }
  vi.stubGlobal("XMLHttpRequest", ErrorXhr);
  const pending = entry === "registered" ? fetchRegisteredAsset("0123456789abcdef")
    : fetchFinancialPdf({ stockCode: "600001", reportYear: 2024, reportPeriod: "FY" } as FinancialMaterial);
  await expect(pending).rejects.toThrow("原文资产已不存在，请重新检索");
});
