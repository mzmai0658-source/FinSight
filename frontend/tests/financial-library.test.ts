import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp } from 'vue';
import FinancialLibrary from '../src/components/report/FinancialLibrary.vue';
const api = vi.hoisted(() => ({ fetchFinancialMaterials: vi.fn(), fetchFinancialPdf: vi.fn(), fetchFinancialPage: vi.fn(), fetchResearchPage: vi.fn(), loadStoredAuth: vi.fn(() => ({})) }));
vi.mock('../src/services/api', () => api);
let app: ReturnType<typeof createApp> | undefined;
afterEach(() => { app?.unmount(); document.body.innerHTML = ''; vi.clearAllMocks(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
async function mount() {
  const el = document.createElement('div'); document.body.append(el);
  app = createApp(FinancialLibrary); app.component('router-link', { props: ['to'], template: '<a><slot /></a>' }); app.mount(el);
  await settle(); return el;
}
describe('financial catalogue states', () => {
  it('renders returned company and PDF identity, and clearly reports original loading failure', async () => {
    api.fetchFinancialMaterials.mockResolvedValue({ total: 1, records: [{ company: '金花股份', stockCode: '600080', reportYear: 2023, reportPeriod: 'HY', fileName: '600080_20230819_U8CH.pdf', pageCount: 127, sha256: 'hash', pdfAvailable: true, status:'imported' }] });
    api.fetchFinancialPdf.mockRejectedValue(new Error('网络超时'));
    const el = await mount();
    expect(el.textContent).toContain('共 1 份已收录财报');
    expect(el.textContent).toContain('2023年上半年财报');
    expect(el.textContent).toContain('127 页');
    (Array.from(el.querySelectorAll('button')).find(b => b.textContent === '查看原件')!).click();
    await settle(); expect(el.querySelector('[role=alert]')?.textContent).toContain('网络超时');
  });
  it('does not present network failure as an empty library and supports retry', async () => {
    api.fetchFinancialMaterials.mockRejectedValueOnce(new Error('目录连接失败')).mockResolvedValue({ records: [], total: 0 });
    const el = await mount();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('目录连接失败');
    expect(el.textContent).not.toContain('尚无已入库财报');
    el.querySelector<HTMLButtonElement>('[role=alert] button')!.click(); await settle();
    expect(el.textContent).toContain('尚无已入库财报');
  });
  it('ignores a slower earlier catalogue response', async () => {
    let resolveOld!: (v: any) => void;
    api.fetchFinancialMaterials.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; })).mockResolvedValueOnce({ records: [], total: 0 });
    const el = await mount();
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle();
    resolveOld({ records: [], total: 99 }); await settle();
    expect(el.textContent).toContain('共 0 份'); expect(el.textContent).not.toContain('共 99 份');
  });
  it('allows reading an imported summary without advertising numeric QA', async () => {
    api.fetchFinancialMaterials.mockResolvedValue({ total:1, records:[{materialId:7,company:'金花股份',stockCode:'600080',reportYear:2023,reportPeriod:'HY',reportKind:'summary',status:'text_only',contentState:'ready',fileName:'摘要.pdf',pageCount:5}] });
    api.fetchFinancialPage.mockResolvedValue({page:1,pageCount:5,ocrPageCount:5,markdown:'# 摘要正文',ocrAvailable:true,contentSource:'ocr'});
    const el=await mount();
    expect(el.textContent).toContain('财报摘要');expect(el.textContent).not.toContain('用这份财报提问');
    [...el.querySelectorAll('button')].find(b=>b.textContent==='阅读正文')!.click();await settle();
    expect(api.fetchFinancialPage).toHaveBeenCalledWith(7,1);expect(document.querySelector('[role=dialog]')?.textContent).toContain('摘要正文');
  });
  it('labels a warning snapshot as partial while keeping its numeric QA entry', async () => {
    api.fetchFinancialMaterials.mockResolvedValue({ total:1, records:[{
      materialId:8,company:'待复核公司',stockCode:'000002',reportYear:2025,reportPeriod:'FY',
      reportKind:'full',status:'imported',qualityStatus:'warn',
      qualityWarnings:['cashflow_incomplete(2/4)'],missingFields:['investing_cf_net_amount'],
      fileName:'000002_2025.pdf',pageCount:150,pdfAvailable:true,sha256:'hash',
    }] });
    const el=await mount();
    expect(el.textContent).toContain('已结构化（部分字段待复核）');
    expect(el.textContent).toContain('已有字段可以问答；缺失或证据不足的字段会拒答。');
    expect([...el.querySelectorAll('a')].some(link=>link.textContent==='用这份财报提问')).toBe(true);
  });
});
