import { vi } from "vitest";

// 作品说明：测试环境缺少媒体查询与原生弹窗能力，使用替身补齐浏览器接口。
if (typeof window !== "undefined") {
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn((media: string) => ({
    media, matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) });
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
}
