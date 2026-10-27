import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom", include: ["tests/**/*.test.ts"], clearMocks: true, setupFiles: ["./tests/setup.ts"],
    // 作品说明：单元测试固定相对API地址，避免本机开发配置改变接口契约用例。
    env: { VITE_API_BASE_URL: "" },
  },
});
