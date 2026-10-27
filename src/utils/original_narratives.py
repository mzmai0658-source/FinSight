"""OCR只提供位置线索；用原件中唯一的首尾锚点读取完整原句。"""
import re
from functools import lru_cache
from pathlib import Path
from .original_cells import normalized_positions


def original_sentence_between_anchors(original, candidate):
    text, positions = normalized_positions(original)
    needle, _ = normalized_positions(candidate)
    anchor_size = 16
    if len(needle) < anchor_size * 2 + 1 or not needle.endswith(('。', '；')):
        return None
    starts = [m.start() for m in re.finditer(re.escape(needle[:anchor_size]), text)]
    ends = [m.end() for m in re.finditer(re.escape(needle[-anchor_size:]), text)]
    matches = []

    def quantities(value):
        return re.findall(r'-?\d[\d,]*(?:\.\d+)?(?:%|％)?', value)

    for start in starts:
        # 不从原句中间截取，避免漏掉前置否定或跨句拼接。
        if start and text[start - 1] not in '。；':
            continue
        for end in ends:
            if end - start < anchor_size * 2 + 1 or end - start > 900:
                continue
            span = text[start:end]
            if re.search(r'[。；]', span[:-1]) or quantities(span) != quantities(needle):
                continue
            matches.append(original[positions[start]:positions[end - 1] + 1])
    return matches[0] if len(matches) == 1 else None


def _scope_heading(value):
    from .original_cause_tables import _scope_marker
    from .financial_row_identity import row_key

    compact = re.sub(r'^第[一二三四五六七八九十\d]+节', '', row_key(value))
    if len(compact) > 100:
        return None
    if re.search(r'\.{2,}|…|\d$', compact):
        return None
    if re.search(r'主要(?:控股[、，,及和]*)?参股公司分析|主要控股[、，,及和]*参股公司分析|'
                 r'主要子公司情况|(?:控股|参股|子)公司(?:情况|分析)', compact):
        return 'foreign'
    marker = _scope_marker(compact)
    if marker:
        return marker
    if re.search(r'^(?:合并)?(?:经营情况讨论与分析|管理层讨论与分析|董事会报告|主营业务分析|'
                 r'经营情况分析|主要经营情况|报告期内主要经营情况|报告期内公司从事的主要业务|'
                 r'业务回顾|经营回顾|财务回顾)', compact):
        return 'consolidated'
    return None


@lru_cache(maxsize=32)
def _document_scope_headings(path, size, mtime_ns):
    import fitz

    with fitz.open(path) as document:
        return tuple((page + 1, line) for page in range(len(document))
                     for line in document[page].get_text().splitlines() if _scope_heading(line))


def original_scope_prefix(path, page):
    """范围声明可以延续多页；只缓存原件标题，不读取或改写检索索引。"""
    stat = Path(path).stat()
    return '\n'.join(line for number, line in _document_scope_headings(str(path), stat.st_size, stat.st_mtime_ns)
                     if number < page)


def _nonprose_boundary(value):
    compact = re.sub(r'\s', '', value)
    numbers = re.findall(r'-?\d[\d,]*(?:\.\d+)?', value)
    return (_scope_heading(value) or re.match(r'^\d+(?:/\d+)?$', compact)
            or re.search(r'(?:股份有限公司|有限责任公司).*年度报告', compact)
            or len(numbers) >= 3 and not re.search(r'[，。；]', value))


def original_context_between_anchors(original, candidate, max_chars=1800):
    """唯一连续原件区间；容许跨句定位，交付字符始终取自 PDF 而非 OCR。"""
    text, positions = normalized_positions(original)
    needle, _ = normalized_positions(candidate)
    text, needle = text.replace('：', ':'), needle.replace('：', ':')
    if not needle or len(needle) > max_chars or '|' in candidate or '<' in candidate or not needle.endswith(('。', '；')):
        return None
    exact = [(m.start(), m.end()) for m in re.finditer(re.escape(needle), text)]
    method = 'unique_original_context_literal'
    if len(exact) > 1:
        return None
    matches = exact
    if not matches:
        anchor_size = 16
        if len(needle) < anchor_size * 2 + 1 or not needle.endswith(('。', '；')):
            return None
        starts = [m.start() for m in re.finditer(re.escape(needle[:anchor_size]), text)]
        ends = [m.end() for m in re.finditer(re.escape(needle[-anchor_size:]), text)]
        quantities = lambda value: re.findall(r'-?\d[\d,]*(?:\.\d+)?(?:%|％)?', value)
        matches = [(start, end) for start in starts for end in ends
                   if anchor_size * 2 < end - start <= max_chars
                   and quantities(text[start:end]) == quantities(needle)]
        method = 'unique_original_context_anchors'
    if len(matches) != 1:
        return None
    start, end = matches[0]
    # 补齐原件句首，不能把 OCR 漏掉的“并非”等前缀截掉后再让模型审核。
    begin = max(text.rfind('。', 0, start), text.rfind('；', 0, start)) + 1
    raw_start = positions[begin]
    # 标题本身是范围依据，不把上面的标题、页眉拼入后面的正文句子。
    prefix = original[raw_start:positions[start]]
    for line in re.finditer(r'[^\n]*\n', prefix):
        if _nonprose_boundary(line[0]):
            raw_start = positions[begin] + line.end()
    # 原件行首仅有序号时，候选标签本身有完整物理边界；不跨过它回填前表的
    # 逐行数字。此处只跳过序号，不能跳过“并非”等任何正文前缀。
    match_start = positions[start]
    line_start = original.rfind('\n', 0, match_start) + 1
    line_prefix = original[line_start:match_start]
    if re.fullmatch(r'\s*(?:[（(][一二三四五六七八九十\d]+[）)]|[一二三四五六七八九十\d]+[、.．])\s*', line_prefix):
        raw_start = match_start
    if text[end - 1] not in '。；':
        finish = re.search(r'[。；]', text[end:])
        if finish is None:
            return None
        end += finish.end()
    raw_end = positions[end - 1] + 1
    while raw_start < raw_end and original[raw_start].isspace():
        raw_start += 1
    if raw_end - raw_start > max_chars:
        return None
    return dict(text=original[raw_start:raw_end], start=raw_start, end=raw_end, method=method)


def original_cause_context(original, candidate, metric, year, period, previous=''):
    """原件证明内容、物理范围和明确期间；它不证明候选具有直接因果支持。"""
    from src.agent.v3.cause_evidence import context_metric_pattern
    from .original_cause_tables import _matching_explicit_period

    located = original_context_between_anchors(original, candidate)
    if located is None:
        return None
    literal = located['text']
    metric_match = re.search(context_metric_pattern(metric), literal)
    if metric_match is None:
        return None
    scope = 'consolidated'
    context = previous + '\n' + original[:located['start']]
    heading = ''
    for line in context.splitlines():
        if marker := _scope_heading(line):
            scope = marker
            heading = line
    # OCR可能漏掉位于原件区间内部的标题。逐行检查整个区间，不能把后面的
    # 母公司/分部原因借给前面的合并指标，也不能用末尾的合并标题抹去中间分部。
    for line in re.finditer(r'[^\n]*(?:\n|$)', literal):
        if marker := _scope_heading(line[0]):
            if marker == 'foreign' or marker != scope and literal[:line.start()].strip():
                return None
            scope = marker
            heading = line[0].strip()
    if scope == 'foreign':
        return None
    compact = re.sub(r'\s', '', literal)
    current_years = re.findall(r'((?:19|20)\d{2})年', compact)
    if current_years and str(year) not in current_years:
        return None
    explicit = re.findall(rf'{year}年(?:第[一二三四]季度|前三季度|上半年|半年度|年度|全年|\d{{1,2}}[-—－至]\d{{1,2}}月)', compact)
    if any(not _matching_explicit_period(header, year, period) for header in explicit):
        return None
    return dict(text=literal, narrative_scope=scope,
                original_context=dict(start=located['start'], end=located['end'],
                                      range_verification='original_pdf_text_range', scope_heading=heading),
                location_method=located['method'])
