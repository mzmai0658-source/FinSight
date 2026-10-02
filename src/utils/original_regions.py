"""作品说明：读取有物理边界的单元格，禁止将整页文本拼成一个金额。"""
from functools import lru_cache
from pathlib import Path
import re

@lru_cache(maxsize=128)
def original_region_text(path,size,mtime,page,bbox):
    source=physical_page(path,size,mtime,page)
    if bbox[2]>source.width or bbox[3]>source.height:raise ValueError('Original region outside physical page')
    return source.crop(bbox).extract_text() or ''


@lru_cache(maxsize=64)
def physical_page(path,size,mtime,page):
    """作品说明：只缓存已解析页对象，不保留打开的 PDF 句柄；文件大小及修改时间参与缓存身份，关闭流前完成单页解析。"""
    import pdfplumber
    if page<1:raise ValueError('Original region page outside document')
    with pdfplumber.open(path,pages=[page]) as pdf:
        if len(pdf.pages)!=1:raise ValueError('Original region page outside document')
        source=pdf.pages[0]
        _=source.objects
        return source

def checked_regions(path,proof):
    stat=Path(path).stat()
    regions=[proof.value,proof.row,proof.heading,proof.context]
    if proof.scope_context:regions.append(proof.scope_context)
    if any(original_region_text(str(path),stat.st_size,stat.st_mtime_ns,r.page,tuple(r.bbox))!=r.text for r in regions):return False
    value,row,heading=proof.value,proof.row,proof.heading
    if value.page!=row.page or abs(value.bbox[1]-row.bbox[1])>1 or abs(value.bbox[3]-row.bbox[3])>1:return False
    if row.bbox[2]>value.bbox[0]+1:return False
    if not (heading.bbox[0]<value.bbox[2] and heading.bbox[2]>value.bbox[0]):return False
    if heading.page>value.page or value.page-heading.page>1:return False
    if heading.page==value.page and heading.bbox[3]>value.bbox[1]+1:return False
    return True

def checked_cell_binding(fact,proof):
    """作品说明：依据原件检查口径、期间和单位坐标标注。"""
    compact=lambda value:re.sub(r'\s+','',value).replace('／','/')
    if fact.source.page!=proof.value.page:return False
    label=compact(proof.row.text);heading=compact(proof.heading.text)
    context=compact(proof.context.text)
    from src.utils.disclosed_rates import checked_disclosed_rate
    rate=fact.metric.endswith('_reported_yoy') or fact.metric.endswith('_reported_change_vs_year_end')
    if fact.source.raw_value!=proof.value.text or compact(fact.source.row)!=label or compact(fact.source.column)!=heading:return False
    if rate:
        if not checked_disclosed_rate(fact):return False
        scope=compact(proof.scope_context.text) if proof.scope_context else ''
        if fact.scope!='consolidated' or not any(w in scope for w in ('主要会计数据和财务指标','主要会计数据及财务指标')):return False
    else:
        if fact.period=='Q3' and '年初至报告期末' not in heading:return False
        if fact.period=='Q1' and '本报告期' not in heading:return False
        if fact.period=='HY' and not any(w in heading for w in ('半年度','1-6','1－6','本期')):return False
        if fact.period=='FY' and not any(w in heading for w in (str(fact.year),'本期','本年')):return False
        if any(w in heading for w in ('同比','增减','上年','上期')):return False
    if fact.unit=='元' and not ('单位：'+fact.source.raw_unit in context or '单位:'+fact.source.raw_unit in context or
        '('+fact.source.raw_unit+')' in label or '（'+fact.source.raw_unit+'）' in label):return False
    if fact.unit=='%' and '%' not in label+proof.value.text+(heading if rate else ''):return False
    if fact.unit=='元/股' and '元/股' not in label:return False
    if fact.scope=='parent' and not any(w in context for w in ('母公司现金流量表','母公司利润表','母公司资产负债表')):return False
    if fact.metric=='main_business_revenue':
        scope=compact(proof.scope_context.text) if proof.scope_context else ''
        if not re.search(r'合并财务报表项目(?:注释|附注)',scope) or label!='主营业务':return False
    return True
