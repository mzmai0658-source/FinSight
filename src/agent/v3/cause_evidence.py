"""作品说明：经营原因先绑定原文中的指标主语，避免用利润原因解释收入变化。"""
from __future__ import annotations
import hashlib,re
from .catalog import METRICS

def bound_cause_snippets(snippets,metrics):
    """作品说明：仅裁取明确的指标原因段，保留逐字原文与文件、页码身份；未取得这种证据时说明限制。"""
    excerpts=[]
    for snippet in snippets:
        text=snippet['text']
        for metric in metrics:
            item=METRICS[metric]
            aliases=[a for a in (item.label,*item.aliases) if not re.search(r'[a-z_]',a) and len(a)>=2]
            if metric=='net_profit':aliases.append('净利润')
            label='(?:'+'|'.join(re.escape(a) for a in sorted(set(aliases),key=len,reverse=True))+')'
            # 作品说明：指标必须是段落主语，不接受在另一个指标的原因句中出现的收入、成本等宾语。
            pattern=r'(?:^|\n|[。；])\s*(?:\d+[、.、]\s*)?('+label+r'[^。；\n]{0,70}?(?:变动原因(?:说明)?\s*[:：]|(?:增长|下降|增加|减少|变动)[^。；\n]{0,25}?(?:主要(?:由于|因|系)|原因(?:为|是)|受)))'
            for match in re.finditer(pattern,text):
                start=match.start(1);rest=text[start:]
                end_marker=re.search(r'[。；]',rest)
                if end_marker is None:continue
                end=start+end_marker.end()
                literal=text[start:end]
                if len(literal)>600:continue
                id=hashlib.sha256((snippet['id']+':'+str(start)+':'+str(end)+':'+metric).encode()).hexdigest()
                excerpts.append({**snippet,'id':id,'text':literal,'target_metric':metric,'source_chunk_id':snippet['id'],
                                 'original_start':start,'original_end':end,'cause_binding':'explicit metric subject and causal phrase; verbatim excerpt'})
    return list({s['id']:s for s in excerpts}.values())

def is_evidence_limit(text):
    return bool(re.search(r'尚未|未(?:明确|找到|确认|提供|说明)|没有(?:明确|找到|提供)|缺少(?:依据|证据)|无法(?:确认|获取|确定|解释)|仅提及.*(?:审计|确认)',text))
