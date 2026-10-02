/** 作品说明：平台扩展类型覆盖诊股、研报库、自选、站内信及管理端。 */

export interface AdvisorDimension {
  name: string;
  score: number;
  max: number;
  comment: string;
}

export interface AdvisorReportRow {
  id: number;
  stockCode: string;
  stockName: string;
  riskProfile: string;
  score: number;
  rating: string;
  dimensions: string | null;
  reportMd: string | null;
  status: "GENERATING" | "READY" | "FAILED";
  createdAt?: string;
  updatedAt?: string;
}

export interface AdvisorDiagnosis {
  stockCode: string;
  stockName: string;
  industry: string;
  latestYear: number;
  total: number;
  rating: string;
  dimensions: AdvisorDimension[];
  report: AdvisorReportRow | null;
}

export interface ResearchReportItem {
  id: number;
  title: string;
  reportType: "stock" | "industry";
  stockCode: string;
  stockName: string;
  orgName: string;
  orgSname: string;
  publishDate: string | null;
  industryName: string;
  rating: string;
  lastRating: string;
  researcher: string;
  predictThisYearEps: string;
  predictThisYearPe: string;
  aimPrice: string;
  datasetTag: string;
  pdfPresent: number;
}

export interface PagedResult<T> {
  total: number;
  page: number;
  records: T[];
  stats?: Record<string, number>;
}

export interface WatchlistItem {
  stockCode: string;
  abbr: string | null;
  industry: string | null;
  dataStatus: string | null;
  lastYear: number | null;
  revenue: number | null;
  netProfit: number | null;
  netProfitYoy: number | null;
  roe: number | null;
  createdAt: string;
}

export interface NotificationItem {
  id: number;
  type: string;
  title: string;
  content: string;
  link: string;
  readFlag: number;
  createdAt: string;
}

export interface EtlTaskItem {
  id: number;
  taskUid: string;
  fileName: string;
  fileType: string;
  status: "PENDING" | "RUNNING" | "SUCCESS" | "PARTIAL" | "FAILED";
  stockCode: string;
  reportYear: number | null;
  message: string;
  stepsJson: string | null;
  createdAt: string;
  startedAt: string;
  finishedAt: string;
}

export interface AdminOverview {
  userCount: number;
  sessionCount: number;
  chatTotal: number;
  chatToday: number;
  uvToday: number;
  etlStats: Record<string, number>;
  hotCompanies: { stockCode: string; abbr: string; views: number }[];
}

export interface AdminUserItem {
  id: number;
  username: string;
  nickname: string;
  role: string;
  riskProfile: string;
  status: number;
  createdAt: string;
}

export interface ChatLogItem {
  id: number;
  userId: number;
  sessionUid: string;
  requestId: string;
  question: string;
  status: string;
  durationMs: number;
  createdAt: string;
}
