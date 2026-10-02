import type { LiveChart, LiveStep } from "@/stores/session";
import type {
  ReferenceItem,
  SessionMessage,
  UnifiedEvidence,
  VerificationResult,
  EvidenceFact,
  TurnOutcome,
  AssistantMetadata,
} from "@/types/api";

/** 作品说明：消息渲染视图模型：静态历史消息与流式进行中消息共用同一渲染路径。 */
export interface MessageView {
  datasetProfile?: AssistantMetadata["dataset_profile"];
  responseKind?: AssistantMetadata["response_kind"];
  answerAssessment?: { accepted: boolean; issues?: string[]; tasks?: Array<{ goal: string; status: string; reason?: string }> };
  diagnosticId?: string;
  outcome?: TurnOutcome | null;
  startedAt?: number;
  role: "user" | "assistant";
  content: string;
  live: boolean;
  steps: LiveStep[];
  sql: string;
  rowCount?: number | null;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  charts: LiveChart[];
  references: Array<Partial<ReferenceItem>>;
  verification: VerificationResult | null;
  evidence: UnifiedEvidence[];
  sqlEvidence: UnifiedEvidence[];
  facts: EvidenceFact[];
  needsClarification: boolean;
  clarifyOptions: string[];
  error: string;
  holding?: boolean;
  /** 作品说明：messages 数组中的下标，用于证据栏联动；流式消息为 -1 */
  messageIndex: number;
}

export function viewFromMessage(message: SessionMessage, messageIndex: number): MessageView {
  const meta = message.metadata ?? null;
  const verification = normalizeVerification(meta?.verification ?? meta?.validation?.verification);
  const evidence = meta?.evidence?.length ? meta.evidence : meta?.validation?.evidence ?? [];
  const sqlEvidence = evidence.filter((item) => item.type === "sql");
  if (!sqlEvidence.length && meta?.sql && meta.sql !== "-") {
    for (const [index, sql] of meta.sql.split(";\n").entries()) {
      if (sql.trim()) sqlEvidence.push({ id: `legacy-query-${index + 1}`, type: "sql", sql, source: { label: "历史查询（未保存行证据）" } });
    }
  }
  const steps: LiveStep[] = (meta?.execution_plan ?? []).map((step) => ({
    tool: "plan",
    label: String(step.label ?? step.text ?? step.kind ?? ""),
    detail: String(step.detail ?? ""),
    status: step.status === "waiting" ? "empty" : step.status === "error" || step.status === "fail" ? "error" : step.status === "rejected" ? "rejected" : "done",
  }));

  const charts: LiveChart[] = [];
  const chartDataList = meta?.chart_data_list?.length
    ? meta.chart_data_list
    : meta?.chart_data
      ? [meta.chart_data]
      : [];
  const images = meta?.images ?? [];
  const chartCount = Math.max(chartDataList.length, images.length);
  for (let i = 0; i < chartCount; i += 1) {
    charts.push({
      url: images[i] ?? "",
      chartData: chartDataList[i] ?? null,
      title: chartDataList[i]?.title,
    });
  }

  return {
    role: message.role === "user" ? "user" : "assistant",
    content: message.content,
    live: false,
    steps,
    sql: meta?.sql && meta.sql !== "-" ? meta.sql : "",
    rowCount: sqlEvidence[0]?.row_count,
    rows: sqlEvidence[0]?.rows,
    columns: sqlEvidence[0]?.columns,
    charts,
    references: meta?.references ?? [],
    verification,
    outcome: meta?.outcome,
    diagnosticId: meta?.diagnostics?.id,
    responseKind: meta?.response_kind,
    datasetProfile: meta?.dataset_profile,
    answerAssessment: meta?.answer_assessment,
    evidence,
    sqlEvidence,
    facts: meta?.facts ?? meta?.validation?.facts ?? sqlEvidence.flatMap((query) => query.facts ?? []),
    needsClarification: Boolean(meta?.needs_clarification),
    clarifyOptions: meta?.clarify_options ?? [],
    error: message.error || meta?.error || "",
    messageIndex,
  };
}

export function normalizeVerification(value?: VerificationResult | null): VerificationResult | null {
  if (!value || !["pass", "warn", "fail"].includes(value.status)) return null;
  return { ...value, checks: Array.isArray(value.checks) ? value.checks : [], unmatched_numbers: Array.isArray(value.unmatched_numbers) ? value.unmatched_numbers : [] };
}

export function viewFromStreaming(state: {
  startedAt?: number;
  question: string;
  steps: LiveStep[];
  content: string;
  charts: LiveChart[];
  clarify: { question: string; options: string[] } | null;
  error: string;
  holding?: boolean;
}): { user: MessageView; assistant: MessageView } {
  const lastSqlStep = [...state.steps].reverse().find((step) => step.sql);
  const sqlEvidence: UnifiedEvidence[] = state.steps.filter((step) => step.sql).map((step, index) => ({
    id: step.queryId || `live-query-${index + 1}`, query_id: step.queryId, type: "sql", sql: step.sql,
    status: step.status, rows: step.rows, columns: step.columns, row_count: step.rowCount,
    source: { label: "本轮查询", query_id: step.queryId },
  }));
  return {
    user: {
      role: "user",
      content: state.question,
      live: false,
      steps: [],
      sql: "",
      charts: [],
      references: [],
      verification: null,
      evidence: [],
      sqlEvidence: [],
      facts: [],
      needsClarification: false,
      clarifyOptions: [],
      error: "",
      messageIndex: -1,
    },
    assistant: {
      startedAt: state.startedAt,
      role: "assistant",
      content: state.clarify ? state.clarify.question : state.content,
      live: true,
      steps: state.steps,
      sql: lastSqlStep?.sql ?? "",
      rowCount: undefined,
      rows: lastSqlStep?.rows,
      columns: lastSqlStep?.columns,
      charts: state.charts,
      references: state.steps.flatMap((step) => step.items ?? []),
      verification: null,
      evidence: [],
      sqlEvidence,
      facts: [],
      needsClarification: Boolean(state.clarify),
      clarifyOptions: state.clarify?.options ?? [],
      error: state.error,
      holding: Boolean(state.holding),
      messageIndex: -1,
    },
  };
}
