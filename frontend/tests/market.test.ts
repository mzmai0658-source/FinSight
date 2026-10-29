import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp } from 'vue';
import MarketPage from '../src/pages/MarketPage.vue';
import { toYi, toNum, fmtYi, fmtPct, fmtChartYi, fmtChartPct } from '../src/components/market/chartTheme';

const api = vi.hoisted(() => ({ fetchCompanies: vi.fn(), fetchMarketSummary: vi.fn() }));
vi.mock('../src/services/api', () => api);
vi.mock('vue-router', () => ({ useRoute: () => ({query:{}}), useRouter: () => ({replace:vi.fn(),push:vi.fn()}) }));
vi.mock('../src/components/layout/PortalShell.vue', () => ({default:{template:'<main><slot /></main>'}}));
vi.mock('../src/components/market/EChart.vue', () => ({default:{template:'<div class="test-chart" />'}}));
let app: ReturnType<typeof createApp> | undefined;
afterEach(() => { app?.unmount(); document.body.innerHTML=''; vi.clearAllMocks(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('market data boundaries', () => {
  it('keeps missing amounts and percentages distinct from real zeroes', () => {
    for (const value of [null,undefined,'']) {
      expect(toYi(value)).toBeNull(); expect(toNum(value)).toBeNull();
      for (const format of [fmtYi,fmtPct,fmtChartYi,fmtChartPct]) expect(format(value)).toBe('—');
    }
    expect(toYi('40714960')).toBe(4071.5);
    expect(toYi('0')).toBe(0); expect(toNum(0)).toBe(0); expect(fmtPct(0)).toBe('0.00%');
  });

  it('shows the selected data scope and clear empty states instead of empty chart axes', async () => {
    api.fetchCompanies.mockResolvedValue([]);
    api.fetchMarketSummary.mockResolvedValue({importedCompanies:0,pendingCompanies:0,latestYear:null,
      industryDistribution:[],yearlyAggregate:[],companyRank:[],hotCompanies:[],
      dataset:{label:'试验财报库',companyCount:0,reportCount:0,experimental:true}});
    const el=document.createElement('div'); document.body.append(el);
    app=createApp(MarketPage); app.mount(el); await settle();
    expect(el.textContent).toContain('试验财报库');
    expect(el.textContent).toContain('部分数字尚未完成原件逐项核验');
    expect(el.textContent).toContain('暂无可展示的年报营业收入');
    expect(el.textContent).toContain('暂无可展示的年度指标');
    expect(el.querySelectorAll('.test-chart')).toHaveLength(0);
  });
});
