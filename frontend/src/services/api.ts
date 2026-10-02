import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

import type {
  ApiEnvelope,
  AuthUser,
  HealthResponse,
  ExamplesResponse,
  SessionDetailResponse,
  SessionSummary,
  StreamHandlers,
  TaskView,
  TokenPair,
} from "@/types/api";
import type {
  CompanyDetail,
  CompanyItem,
  CompanySeries,
  HotCompany,
  MarketSummary,
} from "@/types/market";
import type {
  AdminOverview,
  AdminUserItem,
  AdvisorDiagnosis,
  AdvisorReportRow,
  ChatLogItem,
  EtlTaskItem,
  NotificationItem,
  PagedResult,
  ResearchReportItem,
  WatchlistItem,
} from "@/types/platform";

/** 作品说明：所有浏览器 API 和登记资产均经 Java 鉴权，开发态由 Vite 代理 /api。 */
const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/$/, "");

/* 作品说明：token 存储 */

const TOKEN_KEY = "finsight-auth";

export interface StoredAuth {
  accessToken: string;
  refreshToken: string;
  user: AuthUser;
}

export function loadStoredAuth(): StoredAuth | null {
  try {
    const raw = window.localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredAuth;
    if (!parsed.accessToken || !parsed.refreshToken) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveStoredAuth(auth: StoredAuth | null): void {
  if (auth === null) {
    window.localStorage.removeItem(TOKEN_KEY);
  } else {
    window.localStorage.setItem(TOKEN_KEY, JSON.stringify(auth));
  }
}

/** 作品说明：认证失效（刷新也失败）时的回调，由 auth store 注册为登出跳转 */
let onAuthExpired: (() => void) | null = null;

export function setAuthExpiredHandler(handler: () => void): void {
  onAuthExpired = handler;
}

/* 作品说明：刷新单飞 */

let refreshing: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const stored = loadStoredAuth();
    if (!stored) return null;
    try {
      const { data } = await axios.post<ApiEnvelope<TokenPair>>(
        `${API_BASE_URL}/api/auth/refresh`,
        { refreshToken: stored.refreshToken },
      );
      if (data.code !== 0) return null;
      const pair = data.data;
      saveStoredAuth({ accessToken: pair.accessToken, refreshToken: pair.refreshToken, user: pair.user });
      return pair.accessToken;
    } catch {
      return null;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

function handleAuthExpired(): void {
  saveStoredAuth(null);
  onAuthExpired?.();
}

/* 作品说明：axios 实例 */

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const stored = loadStoredAuth();
  if (stored?.accessToken && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${stored.accessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    if (error.response?.status === 401 && config && !config._retried) {
      const token = await refreshAccessToken();
      if (token) {
        config._retried = true;
        config.headers.Authorization = `Bearer ${token}`;
        return api.request(config);
      }
      handleAuthExpired();
    }
    // 作品说明：服务端业务错误（HTTP 4xx/5xx + 统一响应体）→ 还原成携带业务信息的错误
    const envelope = error.response?.data as ApiEnvelope<unknown> | undefined;
    if (envelope && typeof envelope.code === "number" && envelope.message) {
      const bizError = new Error(envelope.message);
      (bizError as Error & { code?: number }).code = envelope.code;
      return Promise.reject(bizError);
    }
    return Promise.reject(error);
  },
);

/** 作品说明：解包统一响应；业务码非 0 抛出携带 message 的错误 */
function unwrap<T>(envelope: ApiEnvelope<T>): T {
  if (envelope.code !== 0) {
    const err = new Error(envelope.message || "请求失败");
    (err as Error & { code?: number }).code = envelope.code;
    throw err;
  }
  return envelope.data;
}

/* 作品说明：认证接口 */

export async function register(payload: {
  username: string;
  password: string;
  nickname?: string;
}): Promise<AuthUser> {
  const { data } = await api.post<ApiEnvelope<AuthUser>>("/api/auth/register", payload);
  return unwrap(data);
}

export async function login(payload: { username: string; password: string }): Promise<TokenPair> {
  const { data } = await api.post<ApiEnvelope<TokenPair>>("/api/auth/login", payload);
  const pair = unwrap(data);
  saveStoredAuth({ accessToken: pair.accessToken, refreshToken: pair.refreshToken, user: pair.user });
  return pair;
}

export async function adminLogin(payload: { username: string; password: string }): Promise<TokenPair> {
  const { data } = await api.post<ApiEnvelope<TokenPair>>("/api/auth/admin/login", payload);
  const pair = unwrap(data);
  saveStoredAuth({ accessToken: pair.accessToken, refreshToken: pair.refreshToken, user: pair.user });
  return pair;
}

export async function logout(): Promise<void> {
  const stored = loadStoredAuth();
  try {
    await api.post("/api/auth/logout", { refreshToken: stored?.refreshToken ?? "" });
  } catch {
    // 作品说明：服务端撤销失败不阻塞本地登出
  }
  saveStoredAuth(null);
}

export async function fetchMe(): Promise<AuthUser> {
  const { data } = await api.get<ApiEnvelope<AuthUser>>("/api/users/me");
  return unwrap(data);
}

/* 作品说明：元信息 */

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await api.get<ApiEnvelope<HealthResponse>>("/api/meta/health");
  return unwrap(data);
}

export function registeredAssetId(raw?: string | null): string {
  const value = String(raw ?? "");
  if (/^[A-Za-z0-9_-]{16,128}$/.test(value)) return value;
  return /^\/api\/assets\/([A-Za-z0-9_-]{16,128})(?:#page=\d+)?$/.exec(value)?.[1] ?? "";
}

export async function fetchRegisteredAsset(assetId: string): Promise<Blob> {
  const id = registeredAssetId(assetId);
  if (!id) throw new Error("原文尚未登记为可访问资产");
  const { data } = await api.get<Blob>(`/api/assets/${id}`, { responseType: "blob" });
  const safeTypes = ["application/pdf", "image/png", "image/jpeg", "image/webp", "text/plain"];
  const type = data.type.split(";")[0];
  return new Blob([data], { type: safeTypes.includes(type) ? type : "text/plain" });
}

export async function fetchExamples(): Promise<ExamplesResponse> {
  const { data } = await api.get<ApiEnvelope<ExamplesResponse>>("/api/meta/examples");
  return unwrap(data);
}

/* 作品说明：多会话 */

export async function createSession(): Promise<{ sessionUid: string; title: string }> {
  const { data } = await api.post<ApiEnvelope<{ sessionUid: string; title: string }>>("/api/chat/sessions");
  return unwrap(data);
}

export async function listSessions(): Promise<SessionSummary[]> {
  const { data } = await api.get<ApiEnvelope<SessionSummary[]>>("/api/chat/sessions");
  return unwrap(data);
}

export async function getSessionDetail(sessionUid: string): Promise<SessionDetailResponse> {
  const { data } = await api.get<ApiEnvelope<SessionDetailResponse>>(`/api/chat/sessions/${sessionUid}`);
  return unwrap(data);
}

export async function deleteSession(sessionUid: string): Promise<void> {
  const { data } = await api.delete<ApiEnvelope<void>>(`/api/chat/sessions/${sessionUid}`);
  unwrap(data);
}

export async function getTask(taskId: string): Promise<TaskView> {
  const response = await api.get<ApiEnvelope<TaskView>>(`/api/chat/tasks/${encodeURIComponent(taskId)}`);
  return response.data.data;
}

export async function recoverTask(clientRequestId: string): Promise<TaskView | null> {
  const response = await api.get<ApiEnvelope<TaskView | null>>("/api/chat/tasks/recover", { params: { clientRequestId } });
  return response.data.data;
}

export async function getActiveTasks(sessionUid: string): Promise<TaskView[]> {
  const response = await api.get<ApiEnvelope<TaskView[]>>(`/api/chat/sessions/${encodeURIComponent(sessionUid)}/tasks`);
  return response.data.data;
}

export async function cancelTask(taskId: string): Promise<TaskView> {
  const response = await api.post<ApiEnvelope<TaskView>>(`/api/chat/tasks/${encodeURIComponent(taskId)}/cancel`);
  return response.data.data;
}

/* 作品说明：行情门户（公开只读） */

export async function fetchMarketSummary(): Promise<MarketSummary> {
  const { data } = await api.get<ApiEnvelope<MarketSummary>>("/api/market/summary");
  return unwrap(data);
}

export async function fetchCompanies(params?: {
  keyword?: string;
  industry?: string;
  status?: string;
}): Promise<CompanyItem[]> {
  const { data } = await api.get<ApiEnvelope<CompanyItem[]>>("/api/market/companies", { params });
  return unwrap(data);
}

export async function fetchCompanyDetail(stockCode: string): Promise<CompanyDetail> {
  const { data } = await api.get<ApiEnvelope<CompanyDetail>>(`/api/market/companies/${stockCode}`);
  return unwrap(data);
}

export async function fetchCompanySeries(stockCode: string): Promise<CompanySeries> {
  const { data } = await api.get<ApiEnvelope<CompanySeries>>(
    `/api/market/companies/${stockCode}/series`,
  );
  return unwrap(data);
}

export async function fetchHotCompanies(limit = 10): Promise<HotCompany[]> {
  const { data } = await api.get<ApiEnvelope<HotCompany[]>>("/api/market/hot", {
    params: { limit },
  });
  return unwrap(data);
}

/* 作品说明：AI 诊股 */

export async function fetchDiagnosis(stockCode: string): Promise<AdvisorDiagnosis> {
  const { data } = await api.get<ApiEnvelope<AdvisorDiagnosis>>(`/api/advisor/${stockCode}`);
  return unwrap(data);
}

export async function requestAdvisorReport(stockCode: string): Promise<{ reportId: number; status: string }> {
  const { data } = await api.post<ApiEnvelope<{ reportId: number; status: string }>>(
    `/api/advisor/${stockCode}/report`,
  );
  return unwrap(data);
}

export async function fetchAdvisorReport(reportId: number): Promise<AdvisorReportRow> {
  const { data } = await api.get<ApiEnvelope<AdvisorReportRow>>(`/api/advisor/reports/${reportId}`);
  return unwrap(data);
}

/* 作品说明：研报库（公开只读） */

export interface FinancialMaterial {
  materialId?: number;
  stockCode: string;
  company: string;
  reportYear: number | null;
  reportPeriod: string;
  fileName: string;
  pageCount: number | null;
  sha256: string | null;
  status: 'imported' | 'text_only';
  structured?: boolean;
  reportKind?: 'full' | 'summary' | 'unclassified';
  identityStatus?: string;
  contentState?: string;
  contentSource?: string;
  textPageCount?: number;
  notes?: string;
  qualityStatus?: 'pass' | 'warn' | 'unknown' | 'not_structured';
  qualityWarnings?: string[];
  missingFields?: string[];
  pdfAvailable: boolean;
}

export async function fetchFinancialMaterials(params: { page?: number; size?: number; keyword?: string }): Promise<{ records: FinancialMaterial[]; total: number; page: number }> {
  const { data } = await api.get<ApiEnvelope<{ records: FinancialMaterial[]; total: number; page: number }>>("/api/materials/financial", { params });
  return unwrap(data);
}

export async function fetchFinancialPdf(item: FinancialMaterial): Promise<Blob> {
  const { data } = await api.get<Blob>(`/api/materials/financial/${encodeURIComponent(item.stockCode)}/${item.reportYear}/${encodeURIComponent(item.reportPeriod)}/file`, { responseType: 'blob', timeout: 60000 });
  if (data.type.split(';')[0] !== 'application/pdf') throw new Error('服务器未返回 PDF 原件，请稍后重试。');
  return data;
}

export async function fetchReports(params: {
  page?: number;
  size?: number;
  keyword?: string;
  stockCode?: string;
  reportType?: string;
}): Promise<PagedResult<ResearchReportItem>> {
  const { data } = await api.get<ApiEnvelope<PagedResult<ResearchReportItem>>>("/api/reports", { params });
  return unwrap(data);
}

export async function fetchInstitutionView(stockCode: string, limit = 6): Promise<ResearchReportItem[]> {
  const { data } = await api.get<ApiEnvelope<ResearchReportItem[]>>("/api/reports/institution-view", {
    params: { stockCode, limit },
  });
  return unwrap(data);
}

export async function fetchReportDetail(id: number): Promise<ResearchReportItem> {
  const { data } = await api.get<ApiEnvelope<ResearchReportItem>>(`/api/reports/${id}`);
  return unwrap(data);
}

export interface ResearchPage {
  fileName: string;
  sha256: string;
  page: number;
  pageCount: number;
  ocrPageCount: number;
  markdown: string;
  ocrAvailable: boolean;
  contentSource?: string;
  contentState?: string;
  notes?: string;
}
export async function fetchResearchPage(id: number, page = 1): Promise<ResearchPage> {
  const { data } = await api.get<ApiEnvelope<ResearchPage>>(`/api/materials/research/${id}/pages`, { params: { page } });
  return unwrap(data);
}

export async function fetchFinancialPage(id: number, page = 1): Promise<ResearchPage> {
  const { data } = await api.get<ApiEnvelope<ResearchPage>>(`/api/materials/financial/${id}/pages`, { params: { page } });
  return unwrap(data);
}

/* 作品说明：自选股 */

export async function fetchWatchlist(): Promise<WatchlistItem[]> {
  const { data } = await api.get<ApiEnvelope<WatchlistItem[]>>("/api/watchlist");
  return unwrap(data);
}

export async function fetchWatchedCodes(): Promise<string[]> {
  const { data } = await api.get<ApiEnvelope<string[]>>("/api/watchlist/codes");
  return unwrap(data);
}

export async function addWatch(stockCode: string): Promise<void> {
  const { data } = await api.post<ApiEnvelope<void>>(`/api/watchlist/${stockCode}`);
  unwrap(data);
}

export async function removeWatch(stockCode: string): Promise<void> {
  const { data } = await api.delete<ApiEnvelope<void>>(`/api/watchlist/${stockCode}`);
  unwrap(data);
}

/* 作品说明：站内信 */

export async function fetchNotifications(page = 1, size = 10): Promise<PagedResult<NotificationItem>> {
  const { data } = await api.get<ApiEnvelope<PagedResult<NotificationItem>>>("/api/notifications", {
    params: { page, size },
  });
  return unwrap(data);
}

export async function fetchUnreadCount(): Promise<number> {
  const { data } = await api.get<ApiEnvelope<number>>("/api/notifications/unread-count");
  return unwrap(data);
}

export async function markNotificationRead(id: number): Promise<void> {
  const { data } = await api.post<ApiEnvelope<void>>(`/api/notifications/${id}/read`);
  unwrap(data);
}

export async function markAllNotificationsRead(): Promise<void> {
  const { data } = await api.post<ApiEnvelope<void>>("/api/notifications/read-all");
  unwrap(data);
}

/* 作品说明：个人资料 */

export async function updateProfile(payload: {
  nickname?: string;
  riskProfile?: string;
}): Promise<AuthUser> {
  const { data } = await api.put<ApiEnvelope<AuthUser>>("/api/users/me", payload);
  const user = unwrap(data);
  const stored = loadStoredAuth();
  if (stored) {
    saveStoredAuth({ ...stored, user });
  }
  return user;
}

/* 作品说明：管理端 */

export async function fetchAdminOverview(): Promise<AdminOverview> {
  const { data } = await api.get<ApiEnvelope<AdminOverview>>("/api/admin/overview");
  return unwrap(data);
}

export async function fetchAdminUsers(params: {
  page?: number;
  size?: number;
  keyword?: string;
}): Promise<PagedResult<AdminUserItem>> {
  const { data } = await api.get<ApiEnvelope<PagedResult<AdminUserItem>>>("/api/admin/users", { params });
  return unwrap(data);
}

export async function updateUserStatus(id: number, status: number): Promise<void> {
  const { data } = await api.put<ApiEnvelope<void>>(`/api/admin/users/${id}/status`, null, {
    params: { status },
  });
  unwrap(data);
}

export async function fetchAdminChatLogs(page = 1, size = 10): Promise<PagedResult<ChatLogItem>> {
  const { data } = await api.get<ApiEnvelope<PagedResult<ChatLogItem>>>("/api/admin/chat-logs", {
    params: { page, size },
  });
  return unwrap(data);
}

export async function uploadEtlFile(file: File, fileType: "financial" | "research"): Promise<EtlTaskItem> {
  const form = new FormData();
  form.append("file", file);
  form.append("fileType", fileType);
  const { data } = await api.post<ApiEnvelope<EtlTaskItem>>("/api/admin/etl/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
  return unwrap(data);
}

export async function fetchEtlTasks(params: {
  page?: number;
  size?: number;
  status?: string;
}): Promise<PagedResult<EtlTaskItem>> {
  const { data } = await api.get<ApiEnvelope<PagedResult<EtlTaskItem>>>("/api/admin/etl/tasks", { params });
  return unwrap(data);
}

export async function retryEtlTask(taskId: number): Promise<EtlTaskItem> {
  const { data } = await api.post<ApiEnvelope<EtlTaskItem>>(`/api/admin/etl/tasks/${taskId}/retry`);
  return unwrap(data);
}

/* 作品说明：SSE 流式问答 */

/**
 * 作品说明：fetch + ReadableStream 解析 text/event-stream。
 * 401 时自动刷新 token 重试一次；事件分发给 handlers。
 */
export class StreamRejectedError extends Error {}

export async function streamChat(
  payload: { sessionUid?: string; question: string; clientRequestId?: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const body = JSON.stringify({
    question: payload.question,
    sessionUid: payload.sessionUid || undefined,
    clientRequestId: payload.clientRequestId || undefined,
  });

  const doFetch = async (token: string | null) =>
    fetch(`${API_BASE_URL}/api/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body,
      signal,
    });

  let response = await doFetch(loadStoredAuth()?.accessToken ?? null);
  if (response.status === 401) {
    const token = await refreshAccessToken();
    if (!token) {
      handleAuthExpired();
      throw new Error("登录已过期");
    }
    response = await doFetch(token);
  }
  // 作品说明：非 SSE 响应（会话忙 409 / 限流 429 / 配额 429 / 5xx）→ 解析统一响应体抛业务错误
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok || !contentType.includes("text/event-stream")) {
    const text = await response.text();
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      message = (JSON.parse(text) as { message?: string }).message ?? message;
    } catch {
      // 作品说明：非 JSON 响应时使用默认提示
    }
    throw new StreamRejectedError(message);
  }
  if (!response.body) {
    throw new Error("当前浏览器不支持流式响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let receivedDone = false;
  let terminalError = "";

  const dispatch = (eventType: string, dataText: string) => {
    let data: unknown;
    try {
      data = JSON.parse(dataText);
    } catch {
      return;
    }
    switch (eventType) {
      case "session":
        handlers.onSession?.(data as never);
        break;
      case "plan":
        handlers.onPlan?.(data as never);
        break;
      case "tool_call":
        handlers.onToolCall?.(data as never);
        break;
      case "tool_result":
        handlers.onToolResult?.(data as never);
        break;
      case "answer_delta":
        handlers.onAnswerDelta?.(data as never);
        break;
      case "chart":
        handlers.onChart?.(data as never);
        break;
      case "clarify":
        handlers.onClarify?.(data as never);
        break;
      case "error":
        if ((data as { terminal?: boolean }).terminal) {
          terminalError = (data as { message?: string }).message ?? "本轮失败，请重试";
        }
        handlers.onError?.(data as never);
        break;
      case "done":
        if (!data || typeof data !== "object" || !(data as { result?: unknown }).result) {
          terminalError = "回答格式不完整，请重试";
          break;
        }
        if (receivedDone) break;
        receivedDone = true;
        handlers.onDone?.(data as never);
        break;
      default:
        break;
    }
  };

  const processBuffer = () => {
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const block = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      let eventType = "";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) {
          eventType = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).trim());
        }
      }
      if (eventType && dataLines.length) {
        dispatch(eventType, dataLines.join("\n"));
      }
      separatorIndex = buffer.indexOf("\n\n");
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    processBuffer();
  }
  buffer += decoder.decode();
  buffer = buffer.replace(/\r\n/g, "\n");
  processBuffer();
  if (!receivedDone) throw new Error(terminalError || "连接已结束，但未收到完整回答，请重新打开会话核对状态或重试。");
}

export function assetUrl(raw?: string | null): string {
  if (!raw) return "";
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    return raw;
  }
  const base = API_BASE_URL.replace(/\/api$/, "");
  return base ? `${base}${raw}` : raw;
}
