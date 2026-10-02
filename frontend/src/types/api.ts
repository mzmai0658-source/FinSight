export type MessageRole = "user" | "assistant" | "system";

/** 作品说明：Java 后端统一响应包装 */
export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
  traceId?: string;
}

/* 作品说明：认证 */

export interface AuthUser {
  id: number;
  username: string;
  nickname: string;
  role: "USER" | "ADMIN";
  riskProfile: string;
}

export interface TokenPair {
  accessToken: string;
  refreshToken: string;
  accessExpiresInSeconds: number;
  user: AuthUser;
}

/* 作品说明：多会话 */

export interface SessionSummary {
  sessionUid: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ServerMessage {
  id: number;
  role: MessageRole;
  content: string;
  metadata?: AssistantMetadata | null;
  createdAt: string;
}

export interface SessionDetailResponse {
  sessionUid: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
  messages: ServerMessage[];
}

export interface StatusItem {
  ok: boolean;
  detail: string;
}

export interface HealthResponse {
  ocr?: { ok: boolean; detail: string };
  service: StatusItem;
  database: StatusItem;
  knowledge_base: StatusItem;
  llm: StatusItem;
  examples: StatusItem;
}

export interface ExampleItem {
  id: string;
  type: string;
  question: string;
}

export interface ExamplesResponse {
  examples: ExampleItem[];
}

export interface ReferenceItem {
  paper_path: string;
  source_title: string;
  text: string;
  score?: number | null;
  paper_image?: string | null;
  asset_id?: string;
  asset_url?: string;
  source_url?: string;
  document_id?: string;
  chunk_id?: string;
  document_version?: string;
  page_start?: number | null;
  page_end?: number | null;
  section_title?: string;
  stock_code?: string;
  report_year?: number | string;
  report_period?: string;
}

export interface ChartData {
  version?: number;
  decimals?: number | null;
  asset_id?: string;
  asset_url?: string;
  chart_type: string;
  title: string;
  x_label?: string;
  y_label?: string;
  x_data: string[];
  y_data: Array<number | null>;
  unit?: string;
  series_name?: string;
  data_source?: EvidenceSource;
  option?: Record<string, unknown>;
}

export type VerificationStatus = "pass" | "warn" | "fail";

export interface VerificationCheck {
  name: string;
  label: string;
  status: VerificationStatus;
  detail: string;
  total?: number;
  matched?: number;
  unmatched?: unknown[];
  failures?: string[];
  warnings?: string[];
}

export interface VerificationResult {
  version?: number;
  numeric?: unknown[];
  request?: unknown[];
  evidence?: unknown[];
  status: VerificationStatus;
  checks: VerificationCheck[];
  unmatched_numbers: Array<{ raw?: string; value?: number; unit?: string }>;
  summary?: { passed: number; warnings: number; failed: number };
  scope?: string;
  unverified_reasons?: string[];
}

export interface EvidenceSource {
  source_url?: string;
  page_start?: number | null;
  page_end?: number | null;
  status?: string;
  kind?: string;
  label?: string;
  title?: string;
  path?: string;
  detail?: string;
  sql?: string;
  query_index?: number;
  row_count?: number;
  asset_id?: string;
  asset_url?: string;
  query_id?: string;
  x_field?: string;
  y_field?: string;
  unit?: string;
  points?: Array<{ row_id?: string; fact_id?: string }>;
  [key: string]: unknown;
}

export interface UnifiedEvidence {
  id: string;
  type: "sql" | "reference" | "chart" | "asset";
  source: EvidenceSource;
  sql?: string;
  status?: string;
  row_count?: number | null;
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  text?: string;
  score?: number | null;
  paper_image?: string | null;
  chart_data?: ChartData;
  asset_id?: string;
  query_id?: string;
  facts?: EvidenceFact[];
}

export interface EvidenceFact {
  id?: string;
  metric?: string;
  scope?: "consolidated" | "parent";
  label?: string;
  value_exact?: string;
  data_version?: string;
  dataset_profile?: { id: string; kind: "real" | "synthetic"; label: string; reports: number; companies: number };
  stock_abbr?: string;
  fact_id: string;
  query_id: string;
  row_id: string;
  stock_code?: string;
  report_year?: string | number;
  report_period?: string;
  field: string;
  unit?: string;
  value: unknown;
  source?: EvidenceSource;
}

export interface ValidationMetadata extends Record<string, unknown> {
  status?: string;
  verification?: VerificationResult;
  evidence?: UnifiedEvidence[];
  facts?: EvidenceFact[];
}

export interface ExecutionStep {
  id?: string;
  kind?: string;
  text?: string;
  step?: number;
  label?: string;
  detail?: string;
  status?: string;
}

export interface TurnOutcome {
  status: "answered" | "partial" | "no_data" | "query_failed" | "needs_clarification" | "unsupported" | "cancelled" | "failed";
  reason_codes: string[];
}

export interface AssistantMetadata {
  version?: number;
  data_version?: string;
  dataset_profile?: { id: string; kind: "real" | "synthetic"; label: string; reports: number; companies: number };
  task_id?: string;
  task_status?: TaskRecordV3["status"];
  deadline?: number;
  verification_v3?: VerificationV3;
  response_kind?: UnderstandingV3["topic"] | "conversation";
  request_contract?: Record<string, unknown>;
  answer_assessment?: { accepted: boolean; issues?: string[]; tasks?: Array<{ goal: string; status: string; reason?: string }> };
  task_results?: Array<Record<string, unknown>>;
  catalog_result?: Record<string, unknown> | null;
  diagnostics?: { id?: string; elapsed_seconds?: number; model_calls?: number | Array<Record<string, unknown>>; timings?: Array<Record<string, unknown>>; errors?: Array<{ code: string; stage: string; detail: string }> };
  dialogue_state?: Record<string, unknown>;
  chart_eligibility?: Array<Record<string, unknown>>;
  outcome?: TurnOutcome | null;
  query_plan?: Record<string, unknown> | null;
  derived_facts?: Array<Record<string, unknown>>;
  comparisons?: Array<Record<string, unknown>>;
  query_trace?: Array<Record<string, unknown>>;
  facts?: EvidenceFact[];
  error?: string;
  persistence_status?: "saved" | "failed";
  sql: string;
  chart_format: string;
  chart_data?: ChartData | null;
  chart_data_list?: ChartData[];
  images: string[];
  references: ReferenceItem[];
  verification?: VerificationResult;
  evidence?: UnifiedEvidence[];
  validation: ValidationMetadata;
  execution_plan: ExecutionStep[];
  needs_clarification: boolean;
  clarify_options?: string[];
  context: Record<string, unknown>;
}

export interface SessionMessage {
  role: MessageRole;
  content: string;
  ts: string;
  metadata?: AssistantMetadata | null;
  error?: string;
  retryQuestion?: string;
  retryClientRequestId?: string;
  retrySessionUid?: string;
}

export interface SessionResponse {
  session_id: string;
  messages: SessionMessage[];
  latest_context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** 作品说明：一轮对话的最终结果（done 事件 result 字段） */
export interface TurnResult {
  version?: number;
  data_version?: string;
  dataset_profile?: { id: string; kind: "real" | "synthetic"; label: string; reports: number; companies: number };
  task_id?: string;
  task_status?: TaskRecordV3["status"];
  deadline?: number;
  verification_v3?: VerificationV3;
  response_kind?: UnderstandingV3["topic"] | "conversation";
  request_contract?: Record<string, unknown>;
  answer_assessment?: AssistantMetadata["answer_assessment"];
  task_results?: Array<Record<string, unknown>>;
  catalog_result?: Record<string, unknown> | null;
  diagnostics?: AssistantMetadata['diagnostics'];
  dialogue_state?: Record<string, unknown>;
  chart_eligibility?: Array<Record<string, unknown>>;
  outcome?: TurnOutcome | null;
  query_plan?: Record<string, unknown> | null;
  derived_facts?: Array<Record<string, unknown>>;
  comparisons?: Array<Record<string, unknown>>;
  query_trace?: Array<Record<string, unknown>>;
  error?: string;
  facts?: EvidenceFact[];
  question: string;
  answer: {
    content: string;
    image: string[];
    references: ReferenceItem[];
  };
  sql: string;
  chart_format: string;
  chart_data?: ChartData | null;
  chart_data_list?: ChartData[];
  execution_plan: ExecutionStep[];
  verification?: VerificationResult;
  evidence?: UnifiedEvidence[];
  validation: ValidationMetadata;
  context: Record<string, unknown>;
  needs_clarification: boolean;
  clarify_options?: string[];
}

export interface StreamDoneEvent {
  session_uid?: string;
  result: TurnResult;
  persistence_status?: "saved" | "failed";
  error?: string;
}

/* 作品说明：SSE 流式事件 */

export type StreamEventType =
  | "session"
  | "plan"
  | "tool_call"
  | "tool_result"
  | "answer_delta"
  | "chart"
  | "references"
  | "clarify"
  | "error"
  | "done";

export interface StreamSessionEvent {
  session_uid: string;
  version?: number;
  task_id?: string;
  client_request_id?: string;
  task_status?: TaskRecordV3["status"];
  deadline?: number;
}

export interface StreamPlanEvent {
  label: string;
  detail?: string;
}

export interface StreamToolCallEvent {
  tool: string;
  label: string;
  detail?: string;
}

export interface StreamToolResultEvent {
  query_id?: string;
  tool: string;
  status: string;
  summary?: string;
  sql?: string;
  row_count?: number | null;
  rows?: Array<Record<string, unknown>>;
  columns?: string[];
  items?: Array<Partial<ReferenceItem>>;
}

export interface StreamAnswerDeltaEvent {
  text: string;
}

export interface StreamChartEvent {
  path?: string;
  url?: string;
  chart_data?: ChartData | null;
  title?: string;
}

export interface StreamClarifyEvent {
  question: string;
  options?: string[];
}

export interface StreamErrorEvent {
  message: string;
  terminal?: boolean;
  code?: string;
}

export interface StreamHandlers {
  onSession?: (event: StreamSessionEvent) => void;
  onPlan?: (event: StreamPlanEvent) => void;
  onToolCall?: (event: StreamToolCallEvent) => void;
  onToolResult?: (event: StreamToolResultEvent) => void;
  onAnswerDelta?: (event: StreamAnswerDeltaEvent) => void;
  onChart?: (event: StreamChartEvent) => void;
  onClarify?: (event: StreamClarifyEvent) => void;
  onError?: (event: StreamErrorEvent) => void;
  onDone?: (event: StreamDoneEvent) => void;
}
import type { TaskRecordV3, VerificationV3, UnderstandingV3 } from "./finsight-v3";

export type TaskView = Omit<TaskRecordV3, "result"> & { result?: TurnResult | null };
