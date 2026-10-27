import type { FactV3, SourceV3 } from "./finsight-v3";
import type { EtlTaskItem } from "./platform";

export type FinancialReviewStatus = "PROCESSING" | "WAITING" | "APPROVED" | "REJECTED" | "PUBLISHED";

export interface FinancialSubmission {
  id: number;
  taskUid: string;
  fileName: string;
  status: EtlTaskItem["status"];
  reviewStatus: FinancialReviewStatus;
  candidateId: string | null;
  sourceSha256: string | null;
  stockCode: string | null;
  company: string | null;
  reportYear: number | null;
  reportPeriod: string | null;
  message: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  publishedVersion: string | null;
}

export interface FinancialIdentity {
  stock_code: string;
  company: string;
  year: number;
  period: string;
  aliases: string[];
}

export type CandidateFact = Omit<Partial<FactV3>, "source" | "value"> & {
  value?: string | number | null;
  source?: Partial<SourceV3> | null;
  [key: string]: unknown;
};

export interface FinancialCandidate {
  candidate_id: string;
  status?: string;
  review_status?: FinancialReviewStatus;
  source_sha256?: string;
  identity?: Partial<FinancialIdentity> | null;
  page_count?: number;
  steps?: { name: string; status: string; message?: string }[];
  facts?: CandidateFact[];
  excluded?: unknown;
  narrative_count?: number;
  index?: Record<string, unknown> | null;
  review?: Record<string, unknown> | null;
  quality?: {
    identity_verified?: boolean;
    facts_verified?: boolean;
    reports_verified?: boolean;
    index_verified?: boolean;
    verified_count?: number;
    excluded_count?: number;
    conflicts?: unknown;
    [key: string]: unknown;
  } | null;
}

export interface FinancialReviewRequest {
  decision: "approve" | "reject";
  reason: string;
  identity?: FinancialIdentity;
}

export interface FinancialPublication {
  version: string;
  reports: number;
  facts: number;
  collection: string;
}
