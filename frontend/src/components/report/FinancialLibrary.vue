<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import AppDialog from '@/components/ui/AppDialog.vue';
import { fetchFinancialMaterials, fetchFinancialPdf, loadStoredAuth, type FinancialMaterial } from '@/services/api';
import { reportPeriod } from '@/utils/financialDisplay';
import ResearchReader from './ResearchReader.vue';
const selected=ref<FinancialMaterial|null>(null);
const props = withDefaults(defineProps<{ initialKeyword?: string }>(), { initialKeyword: '' });
const emit = defineEmits<{ search: [keyword: string] }>();
const rows=ref<FinancialMaterial[]>([]), total=ref(0), page=ref(1), keyword=ref(''), loading=ref(false), error=ref('');
keyword.value = props.initialKeyword;
const appliedKeyword = ref(props.initialKeyword);
const loginTarget = () => ({ path: '/login', query: { redirect: window.location.pathname + window.location.search } });
const loggedIn=!!loadStoredAuth();
const pdfUrls=ref<Record<string,string>>({}), pdfErrors=ref<Record<string,string>>({}), pending=ref<Record<string,boolean>>({});
let version=0, disposed=false;
function key(item:FinancialMaterial){return item.materialId ? String(item.materialId) : `${item.stockCode}-${item.reportYear}-${item.reportPeriod}-${item.sha256}`;}
async function load(){
 if(!loggedIn)return;
 const request=++version;loading.value=true;error.value='';
 try{const result=await fetchFinancialMaterials({page:page.value,size:12,keyword:appliedKeyword.value});if(request===version){rows.value=result.records;total.value=result.total;}}
 catch(e){if(request===version)error.value=e instanceof Error?e.message:'财报目录加载失败';}
 finally{if(request===version)loading.value=false;}
}
function search(){page.value=1;appliedKeyword.value=keyword.value.trim();emit('search',appliedKeyword.value);void load();}
function clearSearch(){keyword.value='';search();}
watch(() => props.initialKeyword, (value) => {
 if(value===appliedKeyword.value)return;
 keyword.value=value;appliedKeyword.value=value;page.value=1;void load();
});
function turn(delta:number){page.value+=delta;void load();}
async function openPdf(item:FinancialMaterial){
 const id=key(item);pending.value[id]=true;pdfErrors.value[id]='';
 try{
  const blob=await fetchFinancialPdf(item);
  const digest=await crypto.subtle.digest('SHA-256',await blob.arrayBuffer());
  const hash=Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('');
  if(hash!==item.sha256)throw new Error('原件版本已变化，请刷新资料列表后重试。');
  if(!disposed)pdfUrls.value[id]=URL.createObjectURL(blob);
 }catch(e){if(!disposed)pdfErrors.value[id]=e instanceof Error?`原件未能加载：${e.message}`:'原件加载失败，请重试。';}
 finally{pending.value[id]=false;}
}
/** 作品说明：卡片状态文案与视觉色调分别表达，状态含义保持一致。 */
function statusLabel(item:FinancialMaterial){
 if(item.status==='imported')return item.qualityStatus==='warn'?'已结构化（部分字段待复核）':'已完成结构化入库';
 return item.contentState==='missing'?'正文待补充':item.contentState==='partial'?'部分正文已收录':'正文已收录';
}
function statusTone(item:FinancialMaterial){
 if(item.status==='imported')return item.qualityStatus==='warn'?'warn':'ok';
 return item.contentState==='missing'?'muted':'info';
}
function askQuestion(item:FinancialMaterial){return `${item.company}（${item.stockCode}）${reportPeriod(item.reportYear,item.reportPeriod)}的营业收入是多少？请注明单位并给出原始财报证据。`;}
onMounted(load);
onBeforeUnmount(()=>{disposed=true;version++;Object.values(pdfUrls.value).forEach(url=>URL.revokeObjectURL(url));});
</script>

<template>
  <section class="financial-library">
    <div class="toolbar">
      <p class="toolbar__count">共 {{ total }} 份已收录财报</p>
      <form v-if="loggedIn" class="search" @submit.prevent="search">
        <input v-model="keyword" maxlength="120" placeholder="搜索公司、股票代码或文件名" aria-label="搜索公司财报" />
        <button type="submit" class="btn btn--primary">搜索</button>
        <button type="button" class="btn" :disabled="loading" @click="load">刷新</button>
      </form>
    </div>
    <div v-if="appliedKeyword" class="filter-summary">当前筛选：<strong>{{ appliedKeyword }}</strong><button type="button" class="btn" @click="clearSearch">清除筛选</button></div>
    <p class="intro">全文与摘要分别展示。正文收录后可阅读；已结构化的财报提供数值问答入口，字段待复核时仍可询问已有字段。</p>

    <div v-if="!loggedIn" class="state">登录后可以查看已入库财报。<router-link :to="loginTarget()">前往登录</router-link></div>
    <div v-else-if="error" class="state state--error" role="alert">{{ error }} <button type="button" class="btn" @click="load">重试</button></div>
    <div v-else-if="loading" class="state" role="status">正在加载财报目录…</div>
    <div v-else-if="!rows.length" class="state">{{ appliedKeyword ? '没有匹配的财报，请更换关键词。' : '尚无已入库财报。管理员导入财报后，会在这里显示。' }}<button v-if="appliedKeyword" type="button" class="btn" @click="clearSearch">查看全部财报</button></div>

    <div v-else class="financial-grid">
      <article v-for="item in rows" :key="key(item)" class="card">
        <header class="card__top">
          <span class="status" :class="`status--${statusTone(item)}`">{{ statusLabel(item) }}</span>
          <span v-if="item.reportKind === 'summary'" class="kind">摘要</span>
        </header>

        <h2 class="card__company">{{ item.company }} <small>{{ item.stockCode }}</small></h2>
        <h3 class="card__period">{{ reportPeriod(item.reportYear,item.reportPeriod) }}财报{{ item.reportKind === 'summary' ? '摘要' : '' }}</h3>

        <p class="card__meta">{{ item.pageCount ? `${item.pageCount} 页` : '页数未登记' }} · 公司公开财报</p>
        <p class="card__file" :title="item.fileName">{{ item.fileName }}</p>

        <p v-if="item.identityStatus === 'unresolved'" class="card__note">公司或报告期尚待确认。</p>
        <p v-if="item.status === 'imported' && item.qualityStatus === 'warn'" class="card__note">已有字段可以问答；缺失或证据不足的字段会拒答。</p>

        <div class="actions">
          <router-link v-if="item.status === 'imported'" class="btn btn--primary" :to="{path:'/workspace',query:{q:askQuestion(item)}}">用这份财报提问</router-link>
          <button v-if="item.materialId" type="button" class="btn" @click="selected=item">阅读正文</button>
          <template v-if="item.status === 'imported'">
            <template v-if="pdfUrls[key(item)]">
              <a class="btn" :href="pdfUrls[key(item)]+'#page=1'" target="_blank" rel="noopener noreferrer">打开原件</a>
              <a class="btn" :href="pdfUrls[key(item)]" :download="item.fileName">下载 PDF</a>
            </template>
            <button v-else type="button" class="btn" :disabled="!item.pdfAvailable || pending[key(item)]" @click="openPdf(item)">{{ pending[key(item)] ? '正在加载原件…' : item.pdfAvailable ? '查看原件' : '原件暂不可用' }}</button>
          </template>
        </div>
        <p v-if="pdfErrors[key(item)]" class="card__error" role="alert">{{ pdfErrors[key(item)] }}</p>
      </article>
    </div>

    <nav v-if="total>12" class="pagination" aria-label="财报分页">
      <button type="button" class="btn" :disabled="page<=1 || loading" @click="turn(-1)">上一页</button>
      <span>{{ page }} / {{ Math.ceil(total/12) }}</span>
      <button type="button" class="btn" :disabled="page*12>=total || loading" @click="turn(1)">下一页</button>
    </nav>

    <AppDialog :open="!!selected?.materialId" :label="selected?.fileName || '财报正文'" @close="selected=null">
      <section v-if="selected?.materialId" class="financial-dialog">
        <header>
          <div>
            <strong>{{ selected.company }} · {{ reportPeriod(selected.reportYear,selected.reportPeriod) }}</strong>
            <small>{{ selected.fileName }}</small>
          </div>
          <button type="button" class="btn" aria-label="关闭正文" @click="selected=null">关闭</button>
        </header>
        <ResearchReader :key="selected.materialId" :report-id="selected.materialId" kind="financial" />
      </section>
    </AppDialog>
  </section>
</template>

<style scoped>
.financial-library {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.filter-summary { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--c-text-secondary); }

/* 作品说明：工具栏 */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--sp-3);
}

.toolbar__count {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--c-ink);
}

.search {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--sp-2);
  flex: 0 1 520px;
}

.search input {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 var(--sp-4);
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface);
  color: var(--c-text);
  font-size: var(--fs-sm);
  outline: none;
}

.search input:focus {
  border-color: var(--c-primary-border);
}

.intro {
  margin-top: calc(-1 * var(--sp-2));
  font-size: var(--fs-xs);
  line-height: 1.7;
  color: var(--c-text-tertiary);
}

/* 作品说明：按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  padding: 0 var(--sp-3);
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  background: var(--c-surface);
  color: var(--c-text-secondary);
  font-size: var(--fs-sm);
  white-space: nowrap;
  cursor: pointer;
  transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
}

.btn:hover:not(:disabled) {
  border-color: var(--c-primary-border);
  color: var(--c-primary);
  text-decoration: none;
}

.btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.btn--primary {
  border-color: var(--c-primary);
  background: var(--c-primary);
  color: #fff;
}

.btn--primary:hover:not(:disabled) {
  background: var(--c-primary-hover);
  color: #fff;
}

.search .btn {
  height: 36px;
  padding: 0 var(--sp-4);
}

/* 作品说明：状态 */
.state {
  padding: var(--sp-10) var(--sp-6);
  text-align: center;
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
  background: var(--c-surface);
  border: 1px dashed var(--c-border-strong);
  border-radius: var(--r-lg);
}

.state--error {
  color: var(--c-danger);
  border-color: var(--c-danger-soft);
}

.state .btn {
  margin-left: var(--sp-2);
}

/* 作品说明：卡片 */
.financial-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
  gap: var(--sp-4);
}

.card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: var(--sp-5);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-lg);
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

.card:hover {
  border-color: var(--c-primary-border);
  box-shadow: var(--shadow-md);
}

.card__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: var(--r-full);
  font-size: var(--fs-xs);
  font-weight: 500;
}

.status::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status--ok {
  background: var(--c-success-soft);
  color: var(--c-success);
}

.status--warn {
  background: var(--c-warning-soft);
  color: var(--c-warning);
}

.status--info {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}

.status--muted {
  background: var(--c-bg);
  color: var(--c-text-tertiary);
}

.kind {
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  border: 1px solid var(--c-border);
  border-radius: var(--r-full);
  padding: 1px 8px;
}

.card__company {
  margin-top: var(--sp-4);
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--c-ink);
}

.card__company small {
  margin-left: 4px;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 400;
  color: var(--c-text-tertiary);
}

.card__period {
  margin-top: 2px;
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-text-secondary);
}

.card__meta {
  margin-top: var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
}

.card__file {
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--c-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card__note {
  margin-top: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: var(--c-warning-soft);
  color: var(--c-warning);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: auto;
  padding-top: var(--sp-4);
}

.card__error {
  margin-top: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--c-danger);
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--c-text-secondary);
}

.financial-dialog {
  background: var(--c-surface);
  border-radius: var(--r-lg);
  width: min(900px, 100%);
  padding: var(--sp-6);
}

.financial-dialog header {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--c-surface);
  padding-bottom: var(--sp-3);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
}

.financial-dialog header div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.financial-dialog header strong {
  font-size: var(--fs-lg);
  color: var(--c-ink);
}

.financial-dialog header small {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--c-text-tertiary);
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .search {
    flex: 1 1 100%;
  }

  .financial-dialog {
    padding: var(--sp-4);
  }
}
</style>
