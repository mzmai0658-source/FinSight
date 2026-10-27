"""以原件单元格和对齐表头建立来源证明，不在整页搜寻替代数字。"""
from __future__ import annotations

from functools import lru_cache
import hashlib
from pathlib import Path

from src.agent.v3.contracts import HashedOriginalRegion, OriginalCellProof, OriginalRegion, StatementScopeProof
from src.utils.financial_numbers import cell_decimal, normalize_cell
from src.utils.financial_row_identity import row_key, checked_period_column, checked_row_identity
from src.utils.original_cells import normalized_positions
from src.utils.original_regions import (original_region_text, physical_page, physical_tables,
                                        aligned_continuation, checked_regions, checked_cell_binding,
                                        header_cell, column_role_matches, column_metadata, header_binding_conflict,
                                        nearest_unit_declaration, generic_period_column, report_period_region)
from src.utils.statement_scope import checked_statement_scope, physical_caption, checked_caption_header_boundary, statement_company_status


def _compact(text, numeric=False):
    return normalized_positions(text, ignore_commas=numeric)[0]


@lru_cache(maxsize=16)
def _captions(path, size, mtime):
    import fitz
    found = []
    with fitz.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    text = ''.join(span['text'] for span in line['spans'])
                    identity = physical_caption(text)
                    if identity:
                        found.append({'page': number, 'bbox': tuple(line['bbox']), **identity})
    return tuple(found)


def _region(path, stat, page, bbox):
    value = physical_page(str(path), stat.st_size, stat.st_mtime_ns, page)
    x0, top, x1, bottom = bbox
    bbox = (max(0., x0), max(0., top), min(value.width, x1), min(value.height, bottom))
    text = original_region_text(str(path), stat.st_size, stat.st_mtime_ns, page, bbox)
    return OriginalRegion(page=page, bbox=bbox, text=text)


def _hashed(region):
    return HashedOriginalRegion(page=region.page, bbox=region.bbox,
                                text_sha256=hashlib.sha256(region.text.encode()).hexdigest())


def _overlap(first, second):
    return max(0., min(first[2], second[2]) - max(first[0], second[0]))


def _column_period(source, wanted):
    column = _compact(source.column)
    if wanted['scope'] == 'mixed':
        for marker in ('母公司', '公司', '合并'):
            column = column.replace(marker, '')
    return column


def _header_regions(path, stat, number, table, cells, value, target, diagnostics=None):
    """读取最近的实际表头组，保留由表格线拆分的年份、期间等片段。"""
    candidates = []
    for bbox in cells:
        if bbox[3] > (value.bbox[1] + .5 if number == value.page else table[3]):
            continue
        if _overlap(bbox, value.bbox) <= 0:
            continue
        region = _region(path, stat, number, bbox)
        period = _compact(region.text).replace('母公司', '').replace('公司', '').replace('合并', '')
        is_header=header_cell(str(path),stat.st_size,stat.st_mtime_ns,number,bbox,cells)
        if period and (is_header or cell_decimal(period) is None and period in target):
            candidates.append(region)
    groups=[]
    for region in sorted(candidates,key=lambda r:(r.bbox[1],r.bbox[3])):
        if groups and region.bbox[1]<=max(r.bbox[3] for r in groups[-1])+1:
            groups[-1].append(region)
        else:
            groups.append([region])
    if not groups:return []
    selected=groups[-1]
    bbox=(min(r.bbox[0] for r in selected),min(r.bbox[1] for r in selected),
          max(r.bbox[2] for r in selected),max(r.bbox[3] for r in selected))
    region=_region(path,stat,number,bbox)
    period=_compact(region.text).replace('母公司','').replace('公司','').replace('合并','')
    if period==target:return [region]
    if diagnostics is not None:
        diagnostics.append(dict(classification='contradiction',reason='physical_header_period_conflict',
                                page=number,actual_header=region.text,expected_header=target))
    return []


def _scope_region(path, stat, number, cells, value, scope):
    markers = {'合并'} if scope == 'consolidated' else {'母公司', '公司'}
    found = []
    for bbox in cells:
        if bbox[3] > value.bbox[1]+.5 or _overlap(bbox, value.bbox) <= 0:
            continue
        region = _region(path, stat, number, bbox)
        if _compact(region.text) in {'合并','母公司','公司'}:
            found.append(region)
        else:
            # 期间与口径共用单元格时，口径仍须独立读取实际字符区域。
            page = physical_page(str(path), stat.st_size, stat.st_mtime_ns, number)
            for word in page.crop(bbox).extract_words(x_tolerance=1.5, y_tolerance=2):
                if _compact(word['text']) in {'合并','母公司','公司'}:
                    found.append(_region(path, stat, number,
                        (word['x0']-.2, word['top']-.2, word['x1']+.2, word['bottom']+.2)))
    nearest=max(found,key=lambda r:r.bbox[1]) if found else None
    return nearest if nearest and _compact(nearest.text) in markers else None


def _table_candidates(fact, path, stat, expected, position, wanted, diagnostics):
    source = fact.source
    number = source.page
    tables = physical_tables(str(path), stat.st_size, stat.st_mtime_ns, number)
    target = _column_period(source, wanted)
    for table, cells in tables:
        for bbox in cells:
            value = _region(path, stat, number, bbox)
            if _compact(value.text, True) != expected[position] or cell_decimal(value.text) is None:
                continue
            numbers = []
            for other in cells:
                if abs(other[1]-bbox[1]) > .75 or abs(other[3]-bbox[3]) > .75:
                    continue
                region = _region(path, stat, number, other)
                if cell_decimal(region.text) is not None:
                    numbers.append(region)
            numbers.sort(key=lambda r: r.bbox[0])
            if [_compact(r.text, True) for r in numbers] != expected or numbers[position].bbox != bbox:
                continue
            label = _region(path, stat, number, (table[0], bbox[1], numbers[0].bbox[0]-.1, bbox[3]))
            if _compact(row_key(label.text)) != _compact(row_key(source.row)):
                continue
            before=len(diagnostics)
            headers = _header_regions(path, stat, number, table, cells, value, target, diagnostics)
            header_cells = cells
            if not headers and len(diagnostics)>before:
                continue
            if not headers and number > 1:
                for old_table, old_cells in physical_tables(str(path), stat.st_size, stat.st_mtime_ns, number-1):
                    for header in _header_regions(path, stat, number-1, old_table, old_cells, value, target):
                        if aligned_continuation(str(path), stat.st_size, stat.st_mtime_ns, value, header):
                            headers.append(header)
                            header_cells = old_cells
            for header in headers[:1]:
                column_scope = None
                if wanted['scope'] == 'mixed':
                    column_scope = _scope_region(path, stat, header.page, header_cells, value, fact.scope)
                    if column_scope is None:
                        _,_,scopes=column_metadata(str(path),stat.st_size,stat.st_mtime_ns,number,value.bbox)
                        if scopes:
                            diagnostics.append(dict(classification='contradiction',reason='physical_column_scope_conflict',
                                page=number,actual_scope=max(scopes,key=lambda item:item[0][1])[1],expected_scope=fact.scope))
                        continue
                yield value, label, header, column_scope


@lru_cache(maxsize=128)
def _word_lines(path, size, mtime, number):
    page = physical_page(path, size, mtime, number)
    groups = []
    for word in sorted(page.extract_words(x_tolerance=1.5, y_tolerance=2), key=lambda w: (w['top'], w['x0'])):
        group = next((group for group in reversed(groups[-4:]) if abs(group[0]['top']-word['top']) <= 3), None)
        if group is None:
            groups.append([word])
        else:
            group.append(word)
    return tuple(tuple(sorted(group, key=lambda w:w['x0'])) for group in groups)


def _line_candidates(fact, path, stat, expected, position, wanted):
    """无框单行表使用原始字符边界，仍须通过最近物理表头核验。"""
    source = fact.source
    lines = _word_lines(str(path), stat.st_size, stat.st_mtime_ns, source.page)
    for line in lines:
        numbers = [word for word in line if cell_decimal(word['text']) is not None]
        if [_compact(word['text'], True) for word in numbers] != expected:
            continue
        prefix = [word for word in line if word['x1'] <= numbers[0]['x0']+.1]
        labels = [prefix[:end] for end in range(1,len(prefix)+1)
                  if _compact(row_key(''.join(w['text'] for w in prefix[:end]))) == _compact(row_key(source.row))]
        if not labels:
            continue
        label_words=labels[-1]
        top, bottom = min(w['top'] for w in line)-.25, max(w['bottom'] for w in line)+.25
        word = numbers[position]
        value = _region(path, stat, source.page, (word['x0']-.25, top, word['x1']+.25, bottom))
        label = _region(path, stat, source.page, (min(w['x0'] for w in label_words)-.25, top,
                            max(w['x1'] for w in label_words)+.25, bottom))
        left = (numbers[position-1]['x1']+word['x0'])/2 if position else prefix[-1]['x1']
        if position == 0 and len(numbers) > 1:
            # A blank notes cell is not part of the first amount column. Use the
            # neighboring amount centers to keep its header separate from notes
            # and from a centered statement date above the actual table header.
            center = (word['x0'] + word['x1']) / 2
            next_center = (numbers[1]['x0'] + numbers[1]['x1']) / 2
            left = max(left, center - (next_center - center) / 2)
        right = (word['x1']+numbers[position+1]['x0'])/2 if position+1 < len(numbers) else float('inf')
        target = _column_period(source, wanted)
        headers, fragments, scopes = [], [], []
        for header_line in lines:
            if max(w['bottom'] for w in header_line) >= top:
                continue
            selected = [w for w in header_line if left < (w['x0']+w['x1'])/2 < right]
            clean = _compact(''.join(w['text'] for w in selected))
            if clean and clean in target:
                fragments.append(_region(path, stat, source.page,
                    (min(w['x0'] for w in selected)-.25, min(w['top'] for w in selected)-.25,
                     max(w['x1'] for w in selected)+.25, max(w['bottom'] for w in selected)+.25)))
            if clean in ({'合并'} if fact.scope == 'consolidated' else {'母公司','公司'}):
                scopes.append(_region(path, stat, source.page,
                    (min(w['x0'] for w in selected)-.25, min(w['top'] for w in selected)-.25,
                     max(w['x1'] for w in selected)+.25, max(w['bottom'] for w in selected)+.25)))
        for start in range(len(fragments)):
            for end in range(start+1,len(fragments)+1):
                pieces=fragments[start:end]
                if ''.join(_compact(r.text) for r in pieces)!=target:
                    continue
                header=_region(path,stat,source.page,(min(r.bbox[0] for r in pieces),pieces[0].bbox[1],
                                max(r.bbox[2] for r in pieces),pieces[-1].bbox[3]))
                if _compact(header.text)==target:
                    headers.append(header)
        if headers and (wanted['scope'] != 'mixed' or scopes):
            yield value, label, max(headers, key=lambda r:r.bbox[1]), max(scopes, key=lambda r:r.bbox[1]) if scopes else None


def bind_original_source(fact, path, *, diagnostics=None):
    """绑定唯一物理单元格及最近的期间、口径、单位；明确矛盾单列留痕。"""
    if not fact.source:
        return None
    contradictions=[]
    if not checked_period_column(fact) or not column_role_matches(fact,fact.source.column+fact.source.raw_value):
        if diagnostics is not None:
            diagnostics.append(dict(classification='contradiction',reason='physical_column_role_conflict'
                if not column_role_matches(fact,fact.source.column+fact.source.raw_value) else 'source_period_conflict',page=fact.source.page))
        return None
    if not checked_row_identity(fact):return None
    path = Path(path)
    stat = path.stat()
    source = fact.source
    wanted = physical_caption(source.table)
    if not wanted or wanted['scope'] not in {fact.scope, 'mixed'}:
        return None
    if normalize_cell(source.raw_value, source.raw_unit, fact.unit) != fact.decimal:
        return None
    cells = [value.strip() for value in source.literal.split('|')]
    if not 0 <= source.column_index < len(cells) or cells[source.column_index] != source.raw_value:
        return None
    expected = [(index, _compact(value, True)) for index, value in enumerate(cells[1:], 1)
                if cell_decimal(value) is not None]
    position = next((i for i, (column, _) in enumerate(expected) if column == source.column_index), None)
    if position is None:
        return None
    values = [value for _, value in expected]
    candidates = list(_table_candidates(fact, path, stat, values, position, wanted, contradictions))
    if not candidates:
        candidates = list(_line_candidates(fact, path, stat, values, position, wanted))
    captions = _captions(str(path), stat.st_size, stat.st_mtime_ns)
    results = {}
    for value, label, header, column_scope in candidates:
        conflict=header_binding_conflict(str(path),stat.st_size,stat.st_mtime_ns,value,header)
        if conflict:
            contradictions.append(dict(classification='contradiction',reason=conflict,page=value.page))
            continue
        if wanted['scope']=='mixed':
            _,_,scopes=column_metadata(str(path),stat.st_size,stat.st_mtime_ns,value.page,value.bbox)
            nearest=max(scopes,key=lambda item:item[0][1]) if scopes else None
            if nearest and _compact(nearest[1]) not in ({'合并'} if fact.scope=='consolidated' else {'公司','母公司'}):
                contradictions.append(dict(classification='contradiction',reason='physical_column_scope_conflict',
                    page=value.page,actual_scope=nearest[1],expected_scope=fact.scope))
                continue
        preceding = [item for item in captions if item['family'] == wanted['family'] and
                     (item['page'] < source.page or item['page'] == source.page and item['bbox'][1] <= value.bbox[1]+1)]
        if wanted['family']=='indicators':
            # 表内“主要会计数据”等首列表头不是新的章节标题，不能截断表外单位声明。
            owners=[table for table,_ in physical_tables(str(path),stat.st_size,stat.st_mtime_ns,value.page)
                    if table[0]-1<=value.bbox[0] and table[1]-1<=value.bbox[1]
                    and value.bbox[2]<=table[2]+1 and value.bbox[3]<=table[3]+1]
            preceding=[item for item in preceding if item['page']!=value.page or not any(
                table[0]-1<=item['bbox'][0] and table[1]-1<=item['bbox'][1]
                and item['bbox'][2]<=table[2]+1 and item['bbox'][3]<=table[3]+1 for table in owners)]
        caption = max(preceding, key=lambda item:(item['page'],item['bbox'][1])) if preceding else None
        if caption and caption['scope']!=wanted['scope']:
            contradictions.append(dict(classification='contradiction',reason='physical_statement_scope_conflict',
                                       page=value.page,actual_scope=caption['scope'],expected_scope=wanted['scope']))
            continue
        if not caption or caption['page'] < header.page-1:
            continue
        cb = caption['bbox']
        title = _region(path, stat, caption['page'], (cb[0]-.3,cb[1]-.3,cb[2]+.3,cb[3]+.3))
        if physical_caption(title.text) != wanted:
            continue
        owner_status=statement_company_status(path,title,header)
        if owner_status not in {'confirmed','not_declared'}:
            contradictions.append(dict(classification='contradiction' if owner_status=='contradiction' else 'unresolved',
                reason='physical_statement_company_conflict' if owner_status=='contradiction' else 'physical_statement_company_unproven',
                page=value.page))
            continue
        if not checked_caption_header_boundary(path,title,header):
            continue
        if header.page < value.page:
            if any((item['page'] == header.page and item['bbox'][1] > header.bbox[1] or
                    item['page'] == value.page and item['bbox'][1] < value.bbox[3]) for item in captions):
                continue
        # 标题可位于前页而表头在本页；上下边界必须取自同一物理页。
        context_number=caption['page'] if caption['page']==header.page else header.page
        context_page = physical_page(str(path), stat.st_size, stat.st_mtime_ns, context_number)
        context_top=cb[1]-.3 if context_number==caption['page'] else 0
        context_bottom = value.bbox[3] if context_number == value.page else header.bbox[3]
        if context_bottom <= context_top:
            continue
        context = _region(path, stat, context_number, (0,context_top,context_page.width,context_bottom))
        proof = StatementScopeProof(caption=title, family=wanted['family'], value_region=_hashed(value),
                                    label_region=_hashed(label), column_scope=column_scope)
        cell = OriginalCellProof(value=value,row=label,heading=header,context=context,
                                 scope_context=title if wanted['family'] in {'indicators','notes'} else None)
        if (cell.scope_context is None and fact.period in {'Q1','Q3'} and generic_period_column(_column_period(source,wanted))
            and not fact.metric.endswith(('_reported_yoy','_reported_change_vs_year_end'))):
            cover=report_period_region(str(path),stat.st_size,stat.st_mtime_ns,
                                       fact.stock_code,fact.company,fact.year,fact.period)
            if cover is not None:cell=cell.model_copy(update={'scope_context':cover})
        result = fact.model_copy(update={'source':source.model_copy(update={'scope_proof':proof,'original_cell':cell})})
        if fact.unit=='元':
            from src.etl.canonical_audit import row_unit
            physical_unit=row_unit(label.text,label.text)
            claimed_unit=row_unit(source.row,source.row)
            declaration=nearest_unit_declaration(str(path),stat.st_size,stat.st_mtime_ns,result,cell) if not physical_unit else None
            disclosed=physical_unit or (declaration[3] if declaration else None)
            if ((claimed_unit and claimed_unit!=physical_unit) or
                (disclosed and disclosed[0]!=source.raw_unit) or (declaration and declaration[3] is None)):
                contradictions.append(dict(classification='contradiction',reason='physical_unit_conflict',page=value.page))
                continue
            if declaration:
                unit_page,unit_box,_,_=declaration
                unit_source=physical_page(str(path),stat.st_size,stat.st_mtime_ns,unit_page)
                unit_bottom=(value.bbox[3] if unit_page==value.page else
                             header.bbox[3] if unit_page==header.page else unit_source.height)
                context=_region(path,stat,unit_page,(0,unit_box[1]-.25,unit_source.width,unit_bottom))
                cell=cell.model_copy(update={'context':context})
                result.source=result.source.model_copy(update={'original_cell':cell})
        if checked_regions(path,cell) and checked_cell_binding(result,cell,path) and checked_statement_scope(result,path):
            results[(value.page,value.bbox)] = result
    if len(results)==1:return next(iter(results.values()))
    if not results and diagnostics is not None:diagnostics.extend(contradictions)
    return None


def attach_generated_proofs(facts, path):
    accepted, rejected = [], []
    for fact in facts:
        try:
            value = bind_original_source(fact, path)
        except (ValueError, OSError, IndexError):
            value = None
        if value is not None:
            accepted.append(value)
        else:
            rejected.append(dict(metric=fact.metric, scope=fact.scope,
                                 page=fact.source.page if fact.source else None,
                                 reason='physical_cell_or_scope_unverified'))
    return accepted, rejected
