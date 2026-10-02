import { afterEach, expect, it, vi } from "vitest";
import { fetchRegisteredAsset, saveStoredAuth } from "../src/services/api";

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
