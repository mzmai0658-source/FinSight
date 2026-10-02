/** 作品说明：行情门户相关类型（对应 Java /api/market/**） */

export interface CompanyItem {
  stockCode: string;
  abbr: string;
  fullName: string;
  exchange: string;
  board: string;
  industry: string;
  region: string;
  datasetTag: string;
  dataStatus: "imported" | "pending";
  firstYear: number | null;
  lastYear: number | null;
}

/** 作品说明：年度指标行：键为 SQL 别名（year/revenue/net_profit/roe...），值为数字或 null */
export type YearRow = Record<string, number | string | null>;

export interface CompanyDetail {
  profile: CompanyItem;
  enName: string;
  regCapital: string;
  employees: number;
  latestCore: YearRow;
  coverage: YearRow[];
}

export interface CompanySeries {
  stockCode: string;
  core: YearRow[];
  balance: YearRow[];
  cashflow: YearRow[];
}

export interface HotCompany {
  stockCode: string;
  abbr: string;
  industry: string;
  views: number;
}

export interface MarketSummary {
  importedCompanies: number;
  pendingCompanies: number;
  latestYear: number | null;
  industryDistribution: Array<{ industry: string; total: number; imported: number }>;
  yearlyAggregate: YearRow[];
  companyRank: YearRow[];
  hotCompanies: HotCompany[];
}
