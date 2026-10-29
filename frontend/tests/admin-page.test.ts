import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {createApp} from 'vue';
import AdminPage from '../src/pages/AdminPage.vue';
const api=vi.hoisted(()=>({fetchAdminChatLogs:vi.fn(),fetchAdminOverview:vi.fn(),fetchAdminUsers:vi.fn(),
  fetchEtlTasks:vi.fn(),fetchHealth:vi.fn(),updateUserStatus:vi.fn(),uploadEtlFile:vi.fn(),retryEtlTask:vi.fn()}));
vi.mock('../src/services/api',()=>api);
vi.mock('../src/components/market/PortalNav.vue',()=>({default:{template:'<nav />'}}));
let app:ReturnType<typeof createApp>|undefined;
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
beforeEach(()=>{
  api.fetchAdminOverview.mockResolvedValue({userCount:0,sessionCount:0,chatTotal:0,chatToday:0,uvToday:0,etlStats:{},hotCompanies:[]});
  api.fetchHealth.mockResolvedValue({});
  for(const fetcher of [api.fetchEtlTasks,api.fetchAdminUsers,api.fetchAdminChatLogs])fetcher.mockResolvedValue({records:[],total:0});
});
afterEach(()=>{app?.unmount();document.body.innerHTML='';vi.resetAllMocks();});
async function mount(){const el=document.createElement('div');document.body.append(el);app=createApp(AdminPage);
  app.component('router-link',{props:['to'],template:'<a><slot /></a>'});app.mount(el);await settle();return el;}
function button(root:HTMLElement,label:string){return [...root.querySelectorAll('button')].find(el=>el.textContent?.trim()===label)!;}
describe('admin loading failures',()=>{
  it.each([
    ['数据管线','fetchEtlTasks','任务加载失败','暂无任务'],
    ['用户管理','fetchAdminUsers','用户列表加载失败','暂无用户'],
    ['对话审计','fetchAdminChatLogs','审计日志加载失败','暂无审计日志'],
  ] as const)('reports %s failure and allows retry without claiming the list is empty',async(tab,method,message,empty)=>{
    api[method].mockRejectedValueOnce(new Error('offline'));
    const root=await mount();button(root,tab).click();await settle();
    expect(root.querySelector('[role=alert]')?.textContent).toContain(message);
    expect(root.textContent).not.toContain(empty);
    button(root,'重新加载').click();await settle();
    expect(root.querySelector('[role=alert]')).toBeNull();expect(root.textContent).toContain(empty);
  });
  it('keeps the existing user status when an update fails',async()=>{
    api.fetchAdminUsers.mockResolvedValue({records:[{id:2,username:'test-reader',role:'USER',status:1}],total:1});
    api.updateUserStatus.mockRejectedValue(new Error('offline'));
    const root=await mount();button(root,'用户管理').click();await settle();button(root,'禁用').click();await settle();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('用户状态更新失败');
    expect(button(root,'禁用')).toBeTruthy();expect(root.textContent).toContain('正常');
  });
});
