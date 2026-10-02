import { defineStore } from "pinia";

import {
  createSession,
  deleteSession,
  getSessionDetail,
  listSessions,
  streamChat,
  getTask,
  recoverTask,
  getActiveTasks,
  cancelTask,
  loadStoredAuth,
  StreamRejectedError,
} from "@/services/api";
import type {
  ChartData,
  ReferenceItem,
  ServerMessage,
  SessionMessage,
  SessionSummary,
  TaskView,
} from "@/types/api";

const ACTIVE_KEY = "finsight-active-session";

export interface LiveStep {
  tool: string;
  label: string;
  detail: string;
  status: "running" | "done" | "error" | "empty" | "rejected";
  summary?: string;
  sql?: string;
  queryId?: string;
  rowCount?: number | null;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  items?: Array<Partial<ReferenceItem>>;
}

export interface LiveChart {
  url: string;
  chartData?: ChartData | null;
  title?: string;
}

interface StreamingState {
  taskId?: string;
  clientRequestId?: string;
  deadline?: number;
  cancelRequested?: boolean;
  startedAt?: number;
  active: boolean;
  holding: boolean;
  question: string;
  steps: LiveStep[];
  content: string;
  charts: LiveChart[];
  clarify: { question: string; options: string[] } | null;
  error: string;
}

interface SessionState {
  /** 作品说明：会话列表（按最近活跃排序） */
  sessions: SessionSummary[];
  /** 作品说明：当前打开的会话 */
  activeUid: string;
  messages: SessionMessage[];
  latestContext: Record<string, unknown>;
  loadingSessions: boolean;
  loadingMessages: boolean;
  streaming: StreamingState;
}

function emptyStreaming(): StreamingState {
  return {
    active: false,
    holding: false,
    question: "",
    steps: [],
    content: "",
    charts: [],
    clarify: null,
    error: "",
  };
}

function toSessionMessage(message: ServerMessage): SessionMessage {
  return {
    role: message.role,
    content: message.content,
    ts: message.createdAt,
    metadata: message.metadata ?? null,
  };
}

let abortController: AbortController | null = null;
let pollGeneration = 0;
const pendingKey = () => `finsight-pending-turn:${loadStoredAuth()?.user.id ?? "anonymous"}`;
interface PendingTurn { sessionUid: string; question: string; clientRequestId: string; taskId?: string }
function pendingTurns(): Record<string, PendingTurn> {
  try {
    const value = JSON.parse(window.localStorage.getItem(pendingKey()) || "{}");
    // 作品说明：兼容读取旧单请求表示，保留已有恢复信息。
    return value.clientRequestId ? { [value.clientRequestId]: value } : value;
  } catch { return {}; }
}
function savePending(turn: PendingTurn) {
  window.localStorage.setItem(pendingKey(), JSON.stringify({ ...pendingTurns(), [turn.clientRequestId]: turn }));
}
function forgetPending(clientId?: string) {
  if (!clientId) return;
  const turns = pendingTurns(); delete turns[clientId];
  if (Object.keys(turns).length) window.localStorage.setItem(pendingKey(), JSON.stringify(turns));
  else window.localStorage.removeItem(pendingKey());
}

export const useSessionStore = defineStore("session", {
  state: (): SessionState => ({
    sessions: [],
    activeUid: "",
    messages: [],
    latestContext: {},
    loadingSessions: false,
    loadingMessages: false,
    streaming: emptyStreaming(),
  }),
  getters: {
    isStreaming: (state) => state.streaming.active,
    activeSession: (state) => state.sessions.find((item) => item.sessionUid === state.activeUid) ?? null,
  },
  actions: {
    /* 作品说明：会话列表 */
    async loadSessions() {
      this.loadingSessions = true;
      try {
        this.sessions = await listSessions();
      } finally {
        this.loadingSessions = false;
      }
    },
    /** 作品说明：进入工作台时恢复上次会话；没有则空白待开 */
    async bootstrap() {
      await this.loadSessions();
      const remembered = window.localStorage.getItem(ACTIVE_KEY) ?? "";
      const target = this.sessions.find((item) => item.sessionUid === remembered)
        ?? this.sessions[0]
        ?? null;
      if (target) {
        await this.openSession(target.sessionUid);
      } else {
        this.activeUid = "";
        this.messages = [];
        this.latestContext = {};
      }
      if (!this.streaming.active) {
        try {
          const pending = Object.values(pendingTurns()).find(turn => !this.activeUid || turn.sessionUid === this.activeUid);
          if (pending) {
            const task = await recoverTask(pending.clientRequestId);
            if (task && (!this.activeUid || task.session_uid === this.activeUid)) this.resumeTask(task);
            else if (!task) this.resumePending(pending);
          }
        } catch { /* 作品说明：待发送请求可能尚未到达服务端，保留客户端标识用于恢复。 */ }
      }
    },
    async openSession(sessionUid: string) {
      if (sessionUid === this.activeUid && this.streaming.active) return;
      this.detachTaskView();
      this.loadingMessages = true;
      try {
        const detail = await getSessionDetail(sessionUid);
        this.activeUid = sessionUid;
        this.messages = detail.messages.map(toSessionMessage);
        this.latestContext = this.extractLatestContext();
        window.localStorage.setItem(ACTIVE_KEY, sessionUid);
        const tasks = await getActiveTasks(sessionUid);
        const active = tasks.find((task) => ["queued", "running", "cancelling"].includes(task.status));
        if (active) this.resumeTask(active);
        else for (const turn of Object.values(pendingTurns())) {
          if (turn.sessionUid !== sessionUid) continue;
          const recovered=await recoverTask(turn.clientRequestId);
          if (recovered) this.resumeTask(recovered);
          else this.resumePending(turn);
          break;
        }
      } finally {
        this.loadingMessages = false;
      }
    },
    async newSession() {
      this.detachTaskView();
      const created = await createSession();
      this.activeUid = created.sessionUid;
      this.messages = [];
      this.latestContext = {};
      window.localStorage.setItem(ACTIVE_KEY, created.sessionUid);
      await this.loadSessions();
    },
    async removeSession(sessionUid: string) {
      await deleteSession(sessionUid);
      this.sessions = this.sessions.filter((item) => item.sessionUid !== sessionUid);
      if (this.activeUid === sessionUid) {
        this.activeUid = "";
        this.messages = [];
        this.latestContext = {};
        window.localStorage.removeItem(ACTIVE_KEY);
        if (this.sessions.length > 0) {
          await this.openSession(this.sessions[0].sessionUid);
        }
      }
    },
    extractLatestContext(): Record<string, unknown> {
      for (let i = this.messages.length - 1; i >= 0; i -= 1) {
        const metadata = this.messages[i].metadata;
        if (metadata?.context && Object.keys(metadata.context).length > 0) {
          return metadata.context;
        }
      }
      return {};
    },

    /* 作品说明：流式问答 */
    detachTaskView() {
      if (this.streaming.active) this.rememberTask();
      ++pollGeneration;
      abortController?.abort();
      abortController = null;
      this.streaming = emptyStreaming();
    },
    async stopStreaming() {
      if (!this.streaming.active) return;
      if (this.streaming.cancelRequested) return;
      this.streaming.cancelRequested = true;
      this.streaming.holding = true;
      this.streaming.error = "正在请求停止，等待服务端确认。";
      const clientId=this.streaming.clientRequestId;
      const controller=abortController;
      try {
        const task = this.streaming.taskId
          ? await getTask(this.streaming.taskId)
          : await recoverTask(this.streaming.clientRequestId!);
        if (!task) {
          if (this.streaming.clientRequestId===clientId) this.showUnregisteredRequest();
          return;
        }
        if (this.streaming.clientRequestId===clientId) this.streaming.taskId = task.task_id;
        await cancelTask(task.task_id);
        controller?.abort();
        if (this.streaming.clientRequestId===clientId) void this.waitForTask();
      } catch {
        if (this.streaming.clientRequestId!==clientId) return;
        this.streaming.cancelRequested = false;
        this.streaming.error = "停止尚未确认，任务可能仍在执行。恢复连接后可再次停止。";
        void this.waitForTask();
      }
    },
    rememberTask() {
      if (!this.streaming.clientRequestId) return;
      savePending({
        sessionUid: this.activeUid, question: this.streaming.question,
        clientRequestId: this.streaming.clientRequestId, taskId: this.streaming.taskId,
      });
    },
    resumePending(turn: PendingTurn) {
      this.streaming={...emptyStreaming(),active:true,holding:true,question:turn.question,
        clientRequestId:turn.clientRequestId,taskId:turn.taskId};
      void this.waitForTask();
    },
    showUnregisteredRequest() {
      const {question,clientRequestId}=this.streaming;
      this.messages.push({role:"user",ts:new Date().toISOString(),content:question});
      this.messages.push({role:"assistant",ts:new Date().toISOString(),content:"服务端尚未登记这次请求。可以用原请求重试。",
        error:"请求尚未登记。",retryQuestion:question,retryClientRequestId:clientRequestId,retrySessionUid:this.activeUid});
      this.streaming=emptyStreaming();
      // 作品说明：只有服务端权威任务进入终态后，才清除客户端请求标识。
    },
    async retryMessage(index: number) {
      const message=this.messages[index];
      const question=message?.retryQuestion || this.messages[index-1]?.content;
      if (!question || this.streaming.active) return;
      if (message?.retryClientRequestId) {
        const clientId=message.retryClientRequestId;
        const sessionUid=message.retrySessionUid || this.activeUid;
        this.messages.splice(index-1,2);
        this.activeUid=sessionUid;
        await this.sendQuestion(question,clientId);
      } else await this.sendQuestion(question);
    },
    resumeTask(task: TaskView) {
      this.activeUid = task.session_uid;
      this.streaming = { ...emptyStreaming(), active: true, holding: true,
        taskId: task.task_id, clientRequestId: task.client_request_id, deadline: task.deadline,
        cancelRequested: task.status === "cancelling", question: task.question ?? "",
        error: task.status === "cancelling" ? "正在停止。" : "正在恢复后台任务。" };
      this.rememberTask();
      void this.waitForTask();
    },
    async waitForTask() {
      const generation = ++pollGeneration;
      while (this.streaming.active && generation === pollGeneration) {
        try {
          const task = this.streaming.taskId
            ? await getTask(this.streaming.taskId)
            : await recoverTask(this.streaming.clientRequestId!);
          if (generation !== pollGeneration || !this.streaming.active) return;
          if (!task) { this.showUnregisteredRequest(); return; }
          this.streaming.taskId = task.task_id;
          this.streaming.deadline = task.deadline;
          this.activeUid = task.session_uid;
          this.rememberTask();
          if (["completed", "cancelled", "failed"].includes(task.status)) {
            if (task.saved) {
              const detail = await getSessionDetail(task.session_uid);
              if (generation !== pollGeneration) return;
              this.messages = detail.messages.map(toSessionMessage);
              this.latestContext = this.extractLatestContext();
            } else {
              this.messages.push({ role: "user", ts: new Date().toISOString(), content: this.streaming.question });
              this.messages.push({ role: "assistant", ts: new Date().toISOString(),
                content: task.result?.answer?.content || "任务已结束。",
                error: "本轮未保存。", retryQuestion: this.streaming.question });
            }
            forgetPending(this.streaming.clientRequestId);
            this.streaming = emptyStreaming();
            void this.loadSessions();
            return;
          }
          this.streaming.error = task.status === "cancelling" ? "正在停止，等待执行端确认。" : "后台任务仍在执行。";
        } catch {
          if (generation !== pollGeneration || !this.streaming.active) return;
          this.streaming.error = "连接中断，正在恢复任务状态。";
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    },
    async sendQuestion(question: string, recoveryClientId?: string) {
      if (this.streaming.active) return;
      question = question.replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/gu, "");
      if (!question || [...question].length > 2000) throw new Error("问题须为 1–2000 个字符。");

      this.streaming = {
        ...emptyStreaming(),
        active: true,
        startedAt: Date.now(),
        question,
        clientRequestId: recoveryClientId || crypto.randomUUID(),
      };
      this.rememberTask();
      const controller = new AbortController();
      abortController = controller;
      let streamStarted = false;
      let completed = false;
      const submittedClientId = this.streaming.clientRequestId;
      const submittedSessionUid = this.activeUid;
      const belongsToView = () => this.streaming.active && this.streaming.clientRequestId === submittedClientId;

      const markRunningDone = () => {
        for (const step of this.streaming.steps) {
          if (step.status === "running") step.status = "done";
        }
      };

      try {
        await streamChat(
          {
            sessionUid: this.activeUid || undefined,
            question,
            clientRequestId: this.streaming.clientRequestId,
          },
          {
            onSession: (event) => {
              streamStarted = true;
              savePending({ sessionUid: event.session_uid || submittedSessionUid, question,
                clientRequestId: submittedClientId!, taskId: event.task_id });
              if (!belongsToView()) return;
              if (event.session_uid && event.session_uid !== this.activeUid) {
                // 作品说明：服务端自动建会话
                this.activeUid = event.session_uid;
                window.localStorage.setItem(ACTIVE_KEY, event.session_uid);
              }
              this.streaming.taskId = event.task_id;
              this.streaming.deadline = event.deadline;
              this.rememberTask();
            },
            onPlan: (event) => {
              if (!belongsToView()) return;
              markRunningDone();
              this.streaming.steps.push({
                tool: "plan",
                label: event.label,
                detail: event.detail ?? "",
                status: "running",
              });
            },
            onToolCall: (event) => {
              if (!belongsToView()) return;
              markRunningDone();
              this.streaming.steps.push({
                tool: event.tool,
                label: event.label,
                detail: event.detail ?? "",
                status: "running",
              });
            },
            onToolResult: (event) => {
              if (!belongsToView()) return;
              const step = [...this.streaming.steps]
                .reverse()
                .find((item) => item.tool === event.tool && item.status === "running");
              const status =
                event.status === "success" || event.status === "done"
                  ? "done"
                  : event.status === "empty"
                    ? "empty"
                    : event.status === "rejected"
                      ? "rejected"
                      : "error";
              if (step) {
                step.status = status as LiveStep["status"];
                step.summary = event.summary ?? "";
                step.sql = event.sql;
                step.queryId = event.query_id;
                step.rowCount = event.row_count;
                step.rows = event.rows;
                step.columns = event.columns;
                step.items = event.items;
              }
            },
            onAnswerDelta: (event) => {
              if (!belongsToView()) return;
              markRunningDone();
              this.streaming.content += event.text;
            },
            onChart: (event) => {
              if (!belongsToView()) return;
              if (event.url || event.path || event.chart_data) {
                this.streaming.charts.push({
                  url: event.url ?? event.path ?? "",
                  chartData: event.chart_data,
                  title: event.title,
                });
              }
            },
            onClarify: (event) => {
              if (!belongsToView()) return;
              markRunningDone();
              this.streaming.clarify = {
                question: event.question,
                options: event.options ?? [],
              };
            },
            onError: (event) => {
              if (!belongsToView()) return;
              this.streaming.error = event.message;
            },
            onDone: (event) => {
              if (completed || !this.streaming.active || this.streaming.clientRequestId !== submittedClientId) return;
              // 作品说明：服务端已落库：本地直接由 result 构造两条消息，避免整页刷新
              const now = new Date().toISOString();
              const result = event.result;
              completed = true;
              this.messages = [
                ...this.messages,
                { role: "user", content: question, ts: now },
                {
                  role: "assistant",
                  content: result.answer?.content ?? "",
                  ts: now,
                  metadata: {
                    version: result.version,
                    data_version: result.data_version,
                    dataset_profile: result.dataset_profile,
                    task_id: result.task_id,
                    task_status: result.task_status,
                    deadline: result.deadline,
                    verification_v3: result.verification_v3,
                    response_kind: result.response_kind,
                    request_contract: result.request_contract,
                    answer_assessment: result.answer_assessment,
                    task_results: result.task_results,
                    catalog_result: result.catalog_result,
                    dialogue_state: result.dialogue_state,
                    diagnostics: result.diagnostics,
                    chart_eligibility: result.chart_eligibility,
                    outcome: result.outcome,
                    query_plan: result.query_plan,
                    derived_facts: result.derived_facts,
                    comparisons: result.comparisons,
                    query_trace: result.query_trace,
                    sql: result.sql ?? "-",
                    chart_format: result.chart_format ?? "无",
                    chart_data: result.chart_data ?? null,
                    chart_data_list: result.chart_data_list ?? [],
                    images: result.answer?.image ?? [],
                    references: result.answer?.references ?? [],
                    verification: result.verification,
                    evidence: result.evidence ?? result.validation?.evidence ?? [],
                    facts: result.facts ?? result.validation?.facts ?? [],
                    error: event.error || result.error || "",
                    persistence_status: event.persistence_status,
                    validation: result.validation ?? {},
                    execution_plan: result.execution_plan ?? [],
                    needs_clarification: Boolean(result.needs_clarification),
                    clarify_options: result.clarify_options ?? [],
                    context: result.context ?? {},
                  },
                },
              ];
              this.latestContext = result.dialogue_state?.suspended === true ? {} : result.context && Object.keys(result.context).length > 0
                ? result.context
                : this.latestContext;
              this.streaming = emptyStreaming();
              ++pollGeneration;
              forgetPending(submittedClientId);
            },
          },
          controller.signal,
        );
        if (!completed && belongsToView()) {
          this.streaming.holding = true;
          void this.waitForTask();
        }
        // 作品说明：标题/排序可能已变化，后台静默刷新列表
        this.loadSessions().catch(() => undefined);
      } catch (error) {
        if (completed || !belongsToView()) return;
        if (error instanceof StreamRejectedError) {
          forgetPending(submittedClientId);
          this.streaming.active = false;
          this.streaming.error = error.message;
          throw error;
        }
        if (controller.signal.aborted) {
          return;
        }
        if (streamStarted || this.streaming.clientRequestId) {
          this.streaming.holding = true;
          this.streaming.error = "连接中断，正在恢复后台任务。";
          void this.waitForTask();
          return;
        }
        this.streaming.error = error instanceof Error ? error.message : "请求失败，请稍后重试。";
        this.streaming.active = false;
        throw error;
      } finally {
        if (abortController === controller) {
          abortController = null;
        }
      }
    },
  },
});
