import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import FactCard from '../src/components/chat/FactCard.vue';
import GenerationStatus from '../src/components/chat/GenerationStatus.vue';
import type { LiveStep } from '../src/stores/session';

const apps: ReturnType<typeof createApp>[] = [];
afterEach(() => { apps.splice(0).forEach(app => app.unmount()); document.body.innerHTML = ''; vi.useRealTimers(); });
function mount(component: any, props: any) {
  const el = document.createElement('div'); document.body.append(el);
  const app = createApp(component, props); apps.push(app); app.mount(el); return el;
}
describe('readable evidence and persistent waiting status', () => {
  it('separates ordinary and adjusted ROE, retaining technical IDs behind closed details', () => {
    const base = { stock_abbr: '金花股份', stock_code: '600080', report_year: 2023, report_period: 'HY', unit: '%', fact_id: 'fact-internal', row_id: 'row-internal', query_id: 'query-internal', source: { source_path: 'data_root/report.pdf', page_start: 6 } };
    const ordinary = mount(FactCard, { fact: { ...base, field: 'roe', value: 0.18 } });
    const adjusted = mount(FactCard, { fact: { ...base, field: 'roe_weighted_excl_non_recurring', value: 1.03 } });
    expect(ordinary.textContent).toContain('金花股份（600080）');
    expect(ordinary.textContent).toContain('2023年上半年');
    expect(ordinary.textContent).toContain('普通加权平均净资产收益率');
    expect(ordinary.textContent).toContain('0.18%');
    expect(ordinary.textContent).toContain('report.pdf');
    expect(ordinary.textContent).toContain('第 6 页');
    expect(ordinary.querySelector('details')?.open).toBe(false);
    expect(ordinary.querySelector('details')?.textContent).toContain('fact-internal');
    expect(adjusted.textContent).toContain('扣非加权平均净资产收益率');
    expect(adjusted.textContent).toContain('1.03%');
    const registered = mount(FactCard, { fact: { ...base, field: 'roe', value: 0.18, source: { ...base.source, asset_id: '0123456789abcdef' } } });
    expect(registered.textContent).not.toContain('查看数字原文');
    expect(registered.textContent).toContain('第 6 页');
  });
  it('appears before any event and keeps counting when tools have finished', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-14T00:00:00Z'));
    const props = reactive({ steps: [] as LiveStep[], startedAt: Date.now(), error: '' });
    const el = mount(GenerationStatus, props);
    expect(el.textContent).toContain('已提交');
    props.steps.push({ tool: 'query_database', label: '查询', detail: '', status: 'done' });
    vi.advanceTimersByTime(65000); await nextTick();
    expect(el.textContent).toContain('等待模型整理回答');
    expect(el.textContent).toContain('已等待 65 秒');
    expect(el.textContent).toContain('不是剩余时间');
    apps.pop()!.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('displays the real transport error instead of continued waiting', () => {
    const el = mount(GenerationStatus, { steps: [], error: '连接已断开' });
    expect(el.textContent).toContain('连接已断开');
    expect(el.textContent).not.toContain('请稍候');
  });
});
