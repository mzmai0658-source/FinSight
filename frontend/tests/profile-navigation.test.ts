import { afterEach, expect, it, vi } from "vitest";
import { createApp } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import ProfilePage from "../src/pages/ProfilePage.vue";

const api = vi.hoisted(() => ({
  fetchUnreadCount: vi.fn().mockResolvedValue(0),
  fetchWatchlist: vi.fn(), fetchNotifications: vi.fn(), removeWatch: vi.fn(),
  markAllNotificationsRead: vi.fn(), markNotificationRead: vi.fn(), updateProfile: vi.fn(),
}));
vi.mock("../src/services/api", () => api);
vi.mock("../src/stores/auth", () => ({ useAuthStore: () => ({ user: { nickname: "研究用户", username: "user" }, isAuthenticated: true }) }));
let app: ReturnType<typeof createApp>;
afterEach(() => { app?.unmount(); document.body.innerHTML = ""; vi.clearAllMocks(); });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
async function mount() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/:pathMatch(.*)*", component: { render: () => null } }] });
  await router.push("/profile");
  const root = document.createElement("div"); document.body.append(root);
  app = createApp(ProfilePage); app.use(router); app.mount(root); await settle(); return root;
}

it("shows recoverable loading errors instead of claiming there are no companies or notifications", async () => {
  api.fetchWatchlist.mockRejectedValueOnce(new Error("network")).mockResolvedValue([]);
  api.fetchNotifications.mockRejectedValueOnce(new Error("network")).mockResolvedValue({ records: [], total: 0 });
  const root = await mount();
  expect(root.textContent).toContain("关注列表加载失败");
  expect(root.textContent).toContain("通知加载失败");
  expect(root.textContent).not.toContain("还没有关注的公司");
  expect(root.textContent).not.toContain("暂无通知");
  root.querySelectorAll<HTMLButtonElement>('[role="alert"] button').forEach(button => button.click()); await settle();
  expect(root.textContent).toContain("还没有关注的公司");
  expect(root.textContent).toContain("暂无通知");
});

it("can read notifications beyond the first page", async () => {
  api.fetchWatchlist.mockResolvedValue([]);
  api.fetchNotifications.mockResolvedValueOnce({ total: 9, records: [{id:1,title:"第一条通知",content:"",readFlag:1}] })
    .mockResolvedValueOnce({ total: 9, records: [{id:9,title:"第九条通知",content:"",readFlag:1}] });
  const root = await mount();
  const next = Array.from(root.querySelectorAll<HTMLButtonElement>('[aria-label="通知分页"] button')).find(button => button.textContent?.includes("下一页"))!;
  next.click(); await settle();
  expect(api.fetchNotifications).toHaveBeenLastCalledWith(2, 8);
  expect(root.textContent).toContain("第九条通知");
  expect(next.disabled).toBe(true);
});

it("keeps the followed company visible if removal fails", async () => {
  api.fetchNotifications.mockResolvedValue({ records: [], total: 0 });
  api.fetchWatchlist.mockResolvedValue([{stockCode:"600080",abbr:"金花股份"}]);
  api.removeWatch.mockRejectedValue(new Error("network"));
  const root = await mount();
  root.querySelector<HTMLButtonElement>(".link-btn")!.click(); await settle();
  expect(root.textContent).toContain("取消关注失败");
  expect(root.querySelector(".watch__name")?.textContent).toContain("金花股份");
});
