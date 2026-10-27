"""经营原因表格按原 PDF 物理行核对，不把 OCR 拼接行伪装成逐字原文。"""
from functools import lru_cache
from pathlib import Path
import re

from .original_cells import normalized_positions
from .original_regions import physical_page, header_cell, positive_interval_overlap


def _cell_key(value):
    return normalized_positions(value.replace('\\n', '\n'))[0].replace('：', ':')


def _scope_marker(value, generic=True):
    from .financial_row_identity import row_key

    text = row_key(value)
    if re.search(r'分部|分行业|分产品|分地区|分业务|业务板块|抵[销消]前|'
                 r'财务公司主要(?:财务|会计)|(?:下属|所属|控股|参股)子公司|'
                 r'合并(?:及|和|与)母公司|母公司(?:及|和|与)合并', text):
        return 'foreign'
    subject = r'(?:主要(?:会计|财务)|利润表|资产负债表|现金流量表|财务报表|会计|报表|科目|项目)'
    if re.match(r'母公司(?:单体)?' + subject, text):
        return 'parent'
    if re.match(r'合并' + subject, text):
        return 'consolidated'
    if generic and re.match(r'(?:公司)?主要(?:会计数据|财务指标|财务数据|财务信息)', text):
        return 'consolidated'
    return None


def _ordered_scope(prefix):
    scope = 'consolidated'
    for line in prefix.splitlines():
        if marker := _scope_marker(line):
            scope = marker
    return scope


def _current_column_header(path, size, mtime, page_number, page, table, row, pairs):
    from .financial_numbers import cell_decimal

    current = next((box for box, value in pairs[1:-1] if cell_decimal(value) is not None), None)
    if current is None:
        return ''
    cells = tuple(cell for cell in table.cells if cell is not None)
    headers = []
    for box in cells:
        if box[3] > row.bbox[1] + 1 or not positive_interval_overlap(box[0], box[2], current[0], current[2]):
            continue
        if header_cell(path, size, mtime, page_number, box, cells):
            headers.append((box, page.crop(box).extract_text() or ''))
    return max(headers, key=lambda item: item[0][3])[1] if headers else ''


def _matching_explicit_period(header, year, period):
    from types import SimpleNamespace
    from .financial_row_identity import checked_period_column

    clean = _cell_key(header)
    if not re.search(r'(?:19|20)\d{2}|年度|全年|半年|季度|\d{1,2}[-—－至]\d{1,2}月|\d{1,2}月\d{1,2}日', clean):
        return True
    if year is None or period is None:
        return False
    # 二、三、四季度单季列不证明年初累计；不把报告期元数据覆盖原表明确期间。
    if re.search(r'第[二三四]季度|(?<!前)[二三四]季度', clean):
        return False
    months = re.findall(r'(?<!\d)(\d{1,2})[-—－至](\d{1,2})月', clean)
    expected = {'Q1': (1, 3), 'HY': (1, 6), 'Q3': (1, 9), 'FY': (1, 12)}.get(period)
    if months and any(tuple(map(int, pair)) != expected for pair in months):
        return False
    fact = SimpleNamespace(metric='operating_revenue', year=year, period=period,
                           source=SimpleNamespace(column=clean))
    return checked_period_column(fact)


@lru_cache(maxsize=256)
def _original_cause_row(path, size, mtime, page_number, cells, metric, year, period):
    from src.agent.v3.cause_evidence import narrative_aliases
    from .financial_row_identity import row_key

    aliases = {row_key(alias) for alias in narrative_aliases(metric)}
    if row_key(cells[0]) not in aliases:
        return None
    page = physical_page(path, size, mtime, page_number)
    candidates = []
    for table in page.find_tables():
        # 分部、分产品及财务子公司的表格不证明报告主体的整体原因。
        # 读取表前原件上下文，不能依靠 OCR 元数据中的公司名替代范围核验。
        prefix = page.crop((0, max(0, table.bbox[1] - 120), page.width, table.bbox[1])).extract_text() or ''
        # 页首开始的表格可能把分部或母公司标题留在前页末尾；
        # 翻页不重置报表范围；当前页较近的明确标题可以开始另一范围。
        if page_number > 1 and table.bbox[1] < page.height * .2:
            previous = physical_page(path, size, mtime, page_number - 1)
            prefix = (previous.extract_text() or '')[-2000:] + '\n' + prefix
        scope = _ordered_scope(prefix)
        for row in table.rows:
            regions = [cell for cell in row.cells if cell is not None]
            literals = [page.crop(box).extract_text() or '' for box in regions]
            pairs = [(box, value) for box, value in zip(regions, literals) if value.strip()]
            # 表头本身也能明确母公司、合并或分部范围；按物理顺序使用最近声明。
            if pairs and (marker := _scope_marker(pairs[0][1], generic=False)):
                scope = marker
            if scope not in {'parent', 'consolidated'}:
                continue
            if not pairs or row_key(pairs[0][1]) not in aliases:
                continue
            original_cells = [value for _, value in pairs]
            # 位置、顺序、金额正负和整段原因均需一致；邻行原因和换列不得匹配。
            if len(cells) != len(original_cells) or any(_cell_key(a) != _cell_key(b) for a, b in zip(cells, original_cells)):
                continue
            current_header = _current_column_header(path, size, mtime, page_number, page, table, row, pairs)
            if not _matching_explicit_period(current_header, year, period):
                continue
            row_text = page.crop(row.bbox).extract_text() or ''
            if not row_text:
                continue
            candidates.append(dict(text=row_text, narrative_scope=scope,
                original_table_row=dict(page=page_number, bbox=list(row.bbox),
                    column_literals=original_cells, reason_bbox=list(pairs[-1][0]),
                    reason_literal=pairs[-1][1], context=prefix, current_column_header=current_header,
                    row_verification='original_pdf_table_boundaries')))
    unique = {tuple(value['original_table_row']['bbox']): value for value in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def original_cause_table_row(path, snippet):
    cells = snippet.get('cause_table_cells')
    if not isinstance(cells, list) or len(cells) < 3 or not snippet.get('target_metric'):
        return None
    parsed = [value.strip() for value in snippet['text'].strip().strip('|').split('|')]
    if parsed != cells:
        return None
    try:
        stat = Path(path).stat()
        return _original_cause_row(str(path), stat.st_size, stat.st_mtime_ns,
                                   snippet['page'], tuple(cells), snippet['target_metric'],
                                   snippet.get('year'), snippet.get('period'))
    except (OSError, ValueError, IndexError, KeyError):
        return None
