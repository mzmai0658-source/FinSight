import { afterEach, expect, it, vi } from 'vitest';
import { createApp } from 'vue';
import ResearchReader from '../src/components/report/ResearchReader.vue';
import { renderOcrMarkdown } from '../src/utils/markdown';
const api = vi.hoisted(() => ({ fetchResearchPage: vi.fn(), fetchFinancialPage: vi.fn(), loadStoredAuth: vi.fn(() => ({})) }));
vi.mock('../src/services/api', () => api);
let app: ReturnType<typeof createApp>;
afterEach(() => { app?.unmount(); document.body.innerHTML = ''; vi.clearAllMocks(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
it('reads imported pages, supports next page, and labels OCR without claiming RAG verification', async () => {
  api.fetchResearchPage.mockResolvedValueOnce({ fileName:'原始研报.pdf',page:1,pageCount:2,ocrPageCount:2,markdown:'# 正文第一页',ocrAvailable:true })
    .mockResolvedValueOnce({ fileName:'原始研报.pdf',page:2,pageCount:2,ocrPageCount:2,markdown:'正文第二页',ocrAvailable:true });
  const el = document.createElement('div'); document.body.append(el); app=createApp(ResearchReader,{reportId:7});app.component('router-link',{template:'<a><slot /></a>'});app.mount(el);await settle();
  expect(el.textContent).toContain('正文第一页');expect(el.textContent).toContain('可能有识别或排版误差');
  [...el.querySelectorAll('button')].find(b=>b.textContent==='下一页')!.click();await settle();
  expect(api.fetchResearchPage).toHaveBeenLastCalledWith(7,2);expect(el.textContent).toContain('正文第二页');
});
it('preserves OCR table structure while stripping scripts, event handlers and remote images', () => {
  const html = renderOcrMarkdown('<table onclick="alert(1)"><tr><td>营业收入</td><td>100</td></tr></table><script>alert(1)</script><img src="https://example.com/tracker">');
  const el=document.createElement('div');el.innerHTML=html;
  expect(el.querySelectorAll('td')).toHaveLength(2);expect(el.querySelector('[onclick],script,img')).toBeNull();
});
