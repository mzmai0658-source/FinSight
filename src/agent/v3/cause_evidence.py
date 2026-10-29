"""经营原因先绑定原文中的指标主语，避免用利润原因解释收入变化。"""
from __future__ import annotations
import hashlib,re
from .catalog import METRICS

def narrative_aliases(metric):
    item=METRICS[metric]
    aliases={a for a in (item.label,*item.aliases) if not re.search(r'[a-z_]',a) and len(a)>=2}
    if metric=='net_profit':aliases.add('净利润')
    # 现金流净额简称等价于同一科目，不扩展为现金流入量或现金流出量。
    if metric in {'operating_cash_flow','investing_cash_flow','financing_cash_flow'}:
        aliases={a for a in aliases if a.endswith(('净额','净流量'))}
        activity={'operating_cash_flow':'经营','investing_cash_flow':'投资','financing_cash_flow':'筹资'}[metric]
        aliases.update((activity+'活动现金流量净额',activity+'活动产生的现金净流量'))
    return aliases


def alias_pattern(aliases):
    # OCR换行可出现在科目名称内部，位置仍指向未经改写的原文。
    return '(?:'+'|'.join(r'\s*'.join(re.escape(c) for c in a) for a in sorted(aliases,key=len,reverse=True))+')'


def bound_cause_snippets(snippets,metrics):
    """按指标主语或明确比较口径绑定原句，关系不明的叙述不当作已确认原因。"""
    excerpts=[]
    causal=r'变动原因(?:说明)?\s*[:：]|主要\s*(?:由于|因(?:为|系)?|系|是)|原因\s*(?:为|是|系)|(?:增长|下降|增加|减少|变动)[^。；]{0,35}?受'
    for snippet in snippets:
        text=snippet['text']
        # 表格的首列是指标主语，末列才是原因；原因中提及其他指标不改变该行主语。
        # 此处只产生候选，逐字交付前仍须核对原 PDF 的同一物理行和全部单元格。
        from src.utils.financial_numbers import cell_decimal
        from src.utils.financial_row_identity import row_key
        for line in re.finditer(r'[^\n]+',text):
            cells=[value.strip() for value in line[0].strip().strip('|').split('|')]
            if len(cells)<3 or not any(cell_decimal(value) is not None for value in cells[1:-1]):continue
            if not re.search(causal+r'|所致|导致|受[^。]{0,200}影响',cells[-1]):continue
            for metric in sorted(metrics):
                if row_key(cells[0]) not in {row_key(alias) for alias in narrative_aliases(metric)}:continue
                start=line.start()+len(line[0])-len(line[0].lstrip())
                end=line.end()-len(line[0])+len(line[0].rstrip())
                excerpts.append({**excerpt(snippet,metric,start,end,'table metric row and causal cell; requires original row proof'),
                                 'cause_table_cells':cells})
        prefixes=['报告期(?:内)?','本期','本年度','本年','同期','公司','本公司','本集团','集团','该公司','年内',r'20\d{2}\s*年','实现','的',r'[,，\s]']
        if snippet.get('company'):prefixes.append(re.escape(snippet['company']))
        prefix='(?:'+'|'.join(prefixes)+')*'
        for metric in sorted(metrics):
            label=alias_pattern(narrative_aliases(metric))
            pattern=r'(?:^|\n|[。；])\s*(?:[|#*]\s*)*(?:(?:\d+[、.]|[（(]\s*\d+\s*[）)])\s*)?('+prefix+label+r'[^。；|]{0,150}?(?:'+causal+r'))'
            for match in re.finditer(pattern,text):
                subject=re.search(label,match[1]);lead=match[1][subject.end():] if subject else ''
                # 同句先列收入、后转利润原因时，不把后一个指标的原因绑定给收入。
                foreign=[other for other in METRICS if other!=metric and re.search(alias_pattern(narrative_aliases(other)),lead)]
                if foreign:continue
                start=match.start(1);rest=text[start:]
                markers=[m for m in re.finditer(r'[。；]',rest) if m[0]=='。' or not re.match(r'\s*[（(]\s*\d+\s*[）)]',rest[m.end():])]
                end=start+markers[0].end() if markers else min(len(text),start+900)
                if end-start<=900:excerpts.append(excerpt(snippet,metric,start,end,'metric subject and explicit causal relation'))
            # 只接受同时明示指标、前后期间和纳入/剔除业务的完整比較口径原句。
            for sentence in re.finditer(r'[^。；]+[。；]?',text):
                literal=sentence[0]
                if len(literal)>900 or not re.search(label,literal):continue
                scope_basis=(re.search(r'(?:数据|口径|合并范围|报表范围)[\s\S]{0,80}(?:包含|纳入|调整|变化|变更)',literal)
                    and re.search(r'而|相比|不再|已不',literal) and re.search(r'业务|范围|构成|并表',literal)
                    and re.search(r'(?:本年|本期|报告期|20\d{2}\s*年)[\s\S]{0,100}(?:不包含|不再|剔除|不纳入)',literal))
                if scope_basis:
                    prose_start=re.search(r'(?:^|\n)\s*((?:公司|本公司|报告期|本期|本年度|20\d{2}\s*年)[^\n|]{0,100}(?:业绩|数据|口径|合并范围|报表范围))',literal)
                    if prose_start:
                        start=sentence.start()+prose_start.start(1)
                        if re.search(label,text[start:sentence.end()]):
                            excerpts.append(excerpt(snippet,metric,start,sentence.end(),'explicit comparison basis and business scope change'))
    # 重叠窗口按文件、物理页、指标和原句去重，原件核验和独立证据审核仍须通过。
    unique={(s.get('document_version'),s.get('page'),s['target_metric'],s['text']):s for s in excerpts}
    return list(unique.values())[:12]


def excerpt(snippet,metric,start,end,binding):
    identity=hashlib.sha256((snippet['id']+':'+str(start)+':'+str(end)+':'+metric).encode()).hexdigest()
    return {**snippet,'id':identity,'text':snippet['text'][start:end],'target_metric':metric,'source_chunk_id':snippet['id'],
            'original_start':start,'original_end':end,'cause_binding':binding+'; verbatim excerpt'}

def context_metric_pattern(metric):
    """候选可以讨论指标家族；具体指标的因果支持仍由原件和独立审核确认。"""
    aliases = set(narrative_aliases(metric))
    if metric == 'attributable_net_profit':
        # 净利润正文未必逐字重复合并报表行名。这里只开放审核机会，不认定归母关系。
        aliases.add('净利润')
    return alias_pattern(aliases)


def _context_groups(snippets):
    """只拼接同一文件、公司、期间及物理页上可证明相邻的原始块。"""
    keys = ('stock_code', 'company', 'year', 'period', 'document_version', 'source_path', 'page')
    grouped = {}
    isolated = []
    for row in snippets:
        if all(row.get(key) is not None for key in keys) and isinstance(row.get('offset'), int):
            grouped.setdefault(tuple(row[key] for key in keys), []).append(row)
        else:
            isolated.append(row)
    groups = []
    for rows in grouped.values():
        current = None
        for row in sorted(rows, key=lambda value: (value['offset'], value['id'])):
            gap = row['offset'] - (current['offset'] + len(current['text'])) if current else -1
            if current is None or not 0 <= gap <= 4 or len(current['text']) + len(row['text']) > 6000:
                current = {**row, '_ranges': [(0, len(row['text']), row)]}
                groups.append(current)
            else:
                begin = len(current['text']) + gap
                current['text'] += '\n' * gap + row['text']
                current['_ranges'].append((begin, begin + len(row['text']), row))
    groups.extend({**row, '_ranges': [(0, len(row['text']), row)]} for row in isolated)
    return groups


def _prose_ranges(text):
    """保持段落及表格边界，表格不能借正文路径逃过逐格原件核对。"""
    from src.utils.original_narratives import _scope_heading

    begin = None
    end = 0
    for line in re.finditer(r'[^\n]*(?:\n|$)', text):
        value = line[0].strip()
        heading = (not re.search(r'[。；]', value) and _scope_heading(value) or
                   len(value) < 80 and re.match(r'^(?:[一二三四五六七八九十]+[、.]|[（(][一二三四五六七八九十\d]+[）)]|\d+[、.])', value))
        if not value or '|' in value or '<' in value or heading:
            if begin is not None:
                yield begin, end
            begin = None
        else:
            if begin is None:
                begin = line.start() + len(line[0]) - len(line[0].lstrip())
            end = line.start() + len(line[0].rstrip())
    if begin is not None:
        yield begin, end


def _context_window(text, start, end, match, limit=1200):
    end = max(text.rfind('。', start, end), text.rfind('；', start, end)) + 1
    if end <= match:
        return None
    if end - start <= limit:
        return start, end
    sentences = list(re.finditer(r'[^。；]+[。；]?', text[start:end]))
    center = next((i for i, value in enumerate(sentences)
                   if value.start() <= match - start < value.end()), None)
    if center is None:
        return None
    left, right = max(0, center - 1), min(len(sentences) - 1, center + 2)
    while sentences[right].end() - sentences[left].start() > limit and (left < center or right > center):
        if right > center:
            right -= 1
        else:
            left += 1
    begin, finish = start + sentences[left].start(), start + sentences[right].end()
    return (begin, finish) if finish - begin <= limit else None


def _incidental_metric(text, match, metric):
    begin = max(text.rfind('。', 0, match.start()), text.rfind('；', 0, match.start())) + 1
    prefix = text[begin:match.start()]
    if not re.search(r'主要(?:原因为|由于|因|系|是)|原因(?:为|是)|所致|导致', prefix):
        return False
    return any(re.search(alias_pattern(narrative_aliases(other)), prefix)
               for other in METRICS if other != metric and not (
                   metric == 'attributable_net_profit' and other == 'net_profit'))


def cause_evidence_candidates(snippets, metrics):
    """排序待审核原文上下文，不将召回、指标共现或固定句式当作已成立原因。"""
    metrics = set(metrics) & set(METRICS)
    explicit = []
    for value in bound_cause_snippets(snippets, metrics):
        explicit.append({**value, 'evidence_purpose': 'cause_candidate',
                         'cause_binding_type': 'explicit_relation_candidate',
                         'metric_relation': 'named_metric_context', 'support_status': 'unreviewed',
                         'requires_direct_support_review': True,
                         'source_ranges': [dict(chunk_id=value['source_chunk_id'],
                                                start=value['original_start'], end=value['original_end'])]})
    contexts = []
    for group in _context_groups(snippets):
        text = group['text']
        paragraphs = list(_prose_ranges(text))
        for index, (start, end) in enumerate(paragraphs):
            block = text[start:end]
            if not re.search(r'[。；]', block):
                continue
            context_start, context_end = start, end
            if index + 1 < len(paragraphs):
                left, right = paragraphs[index + 1]
                if not text[end:left].strip() and right - start <= 1200:
                    context_end = right
            if index:
                left, right = paragraphs[index - 1]
                if not text[right:start].strip() and context_end - left <= 1200:
                    context_start = left
            for metric in sorted(metrics):
                matches = [m for m in re.finditer(context_metric_pattern(metric), block)
                           if not _incidental_metric(block, m, metric)]
                for match in matches:
                    window = _context_window(text, context_start, context_end, start + match.start())
                    if window is None:
                        continue
                    begin, finish = window
                    literal = text[begin:finish]
                    if len(re.sub(r'\s', '', literal)) < 30:
                        continue
                    if any(value['target_metric'] == metric and value.get('document_version') == group.get('document_version')
                           and value.get('page') == group.get('page') and literal in value['text'] for value in explicit):
                        continue
                    ranges = [dict(chunk_id=row['id'], start=max(begin, left) - left, end=min(finish, right) - left)
                              for left, right, row in group['_ranges'] if left < finish and right > begin]
                    relation = ('named_metric_context' if re.search(alias_pattern(narrative_aliases(metric)), literal)
                                else 'metric_family_context')
                    identity = hashlib.sha256(repr((ranges, metric, group.get('document_version'), group.get('page'))).encode()).hexdigest()
                    value = {key: item for key, item in group.items() if key != '_ranges'}
                    value.update(id=identity, text=literal, target_metric=metric,
                                 evidence_purpose='cause_candidate', cause_binding_type='context_candidate',
                                 metric_relation=relation, support_status='unreviewed', requires_direct_support_review=True,
                                 cause_binding='unreviewed metric context; direct causal support is not established',
                                 source_chunk_id=ranges[0]['chunk_id'], source_ranges=ranges,
                                 original_start=begin, original_end=finish)
                    score = (3 * bool(re.search(r'同比|较上|比上|增长|下降|变动|增加|减少', literal))
                             + 2 * bool(re.search(r'原因|主要|得益|受.*影响|所致|导致', literal))
                             + (relation == 'named_metric_context'))
                    contexts.append((score, value))
    unique = {}
    for _, value in sorted(contexts, key=lambda item: -item[0]):
        key = (value.get('document_version'), value.get('page'), value['target_metric'], value['text'])
        unique.setdefault(key, value)
    return (explicit + list(unique.values()))[:12]


def is_evidence_limit(text):
    return bool(re.search(r'尚未|未(?:明确|找到|确认|提供|说明)|没有(?:明确|找到|提供)|缺少(?:依据|证据)|无法(?:确认|获取|确定|解释)|仅提及.*(?:审计|确认)',text))
