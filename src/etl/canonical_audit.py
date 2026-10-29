"""以原始文件字节核对 OCR 单元格，不依赖宽表标签。口径、列位与单位须由明确报表标题绑定；歧义进入审计，不发布为事实。"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from src.agent.v3.catalog import METRICS, PRIMARY_FAMILIES
from src.agent.v3.contracts import Fact, Source
from src.agent.v3.repository import identity_digest
from src.utils.ocr_json_parser import find_json_cache_for_pdf, iter_layout_pages, read_ocr_json
from src.utils.original_cells import normalized_positions,original_literal
from src.utils.financial_numbers import cell_decimal
from src.utils.report_identity import original_report_identity
from src.etl.reviewed_cells import audited_annotations
from src.utils.disclosed_rates import disclosed_rate_basis
from src.utils.financial_row_identity import row_key,ROW_METRICS
from src.utils.statement_scope import physical_caption, packed_caption_company_status

ROOT = Path(__file__).resolve().parents[2]


def extractor_digest():
    files=('src/etl/canonical_audit.py','src/etl/canonical_narratives.py','src/etl/reviewed_cells.py','src/agent/v3/catalog.py','src/utils/original_cells.py',
        'src/utils/original_regions.py','src/utils/financial_numbers.py','src/utils/report_identity.py',
        'src/utils/disclosed_rates.py','src/utils/financial_row_identity.py',
        'src/utils/statement_scope.py','src/etl/scope_proofs.py','src/agent/v3/contracts.py',
        'data/reference/v3/statement_scope_proofs.json',
        'data/reference/v3/reviewed_original_cells.json')
    return hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in files)).hexdigest()


def original_text_pages(path: Path, reader) -> tuple[list[str], str]:
    """每份原始页只读取一次，保留页界与来源。Poppler 物理布局保留列位置，OCR 缓存不能替代原件验证。"""
    import os
    backend=os.getenv('PDF_TEXT_EXTRACTOR','pymupdf')
    if backend=='pymupdf':
        import fitz
        with fitz.open(path) as document:
            return [page.get_text() for page in document],'pymupdf'
    if backend=='pypdf':
        return [page.extract_text() or '' for page in reader.pages],'pypdf'
    configured=os.getenv('PDFTOTEXT_EXECUTABLE','').strip()
    executable=configured or shutil.which('pdftotext')
    if configured and not Path(configured).is_file():
        raise ValueError('PDFTOTEXT_EXECUTABLE does not point to an installed pdftotext executable')
    if executable:
        extracted=subprocess.run([executable,'-layout','-enc','UTF-8',str(path),'-'],capture_output=True,timeout=120,check=True)
        pages=extracted.stdout.decode('utf-8').split('\f')
        if pages and not pages[-1].strip(): pages.pop()
        if len(pages)==len(reader.pages): return pages,'poppler_layout'
    return [page.extract_text() or '' for page in reader.pages],'pypdf'


def compact(value: str) -> str:
    return re.sub(r'\s+', '', value).replace('，', ',').replace('－', '-').replace('−', '-')


def grid(html: str) -> list[list[str]]:
    """展开跨行跨列单元格，同时保留逻辑列身份。"""
    result, carried = [], {}
    for ri, row in enumerate(BeautifulSoup(html, 'html.parser').find_all('tr')):
        values, ci = [], 0
        for cell in row.find_all(['td', 'th'], recursive=False):
            while (ri, ci) in carried:
                values.append(carried[ri, ci]); ci += 1
            # OCR HTML 中的字面换行转义只用于恢复布局，不改变数字或符号，也不冒充 PDF 原文。
            value = cell.get_text('', strip=True).replace('\\n','\n')
            width, height = int(cell.get('colspan', 1)), int(cell.get('rowspan', 1))
            for dx in range(width):
                values.append(value)
                for dy in range(1, height):
                    carried[ri + dy, ci + dx] = value
            ci += width
        while (ri, ci) in carried:
            values.append(carried[ri, ci]); ci += 1
        result.append(values)
    return result


def ordered_ocr_blocks(page):
    """Keep physical order and expose standalone captions inside packed OCR text."""
    blocks = (page.get('prunedResult') or {}).get('parsing_res_list') or []
    for block in sorted(blocks, key=lambda b: (b.get('block_bbox') or [0, 0])[1]):
        content = block.get('block_content') or ''
        lines = [line for line in content.splitlines() if line.strip()]
        if (block.get('block_label') not in {'table', 'header', 'footer', 'number'} and len(lines) > 1
                and any(physical_caption(line) or row_key(line) == '财务报表' for line in lines)):
            for line in lines:
                yield {**block, 'block_content': line, 'caption_context': content}
        else:
            yield block


def unit_in(text: str) -> tuple[str, Decimal] | None:
    units = set(re.findall(r'单位\s*(?:为\s*[:：]?|[:：])\s*(?:人民币\s*)?(亿元|百万元|万元|千元|元)(?!\s*[/／])', text))
    if len(units) != 1:
        return None
    unit = units.pop()
    return unit, Decimal({'元': '1', '千元': '1000', '万元': '10000', '百万元': '1000000', '亿元': '100000000'}[unit])


def row_unit(label: str, original: str) -> tuple[str, Decimal] | None:
    """金额后缀只有在完整行名得到原件确认时才构成单位依据。"""
    normalized=compact(label)
    match=re.search(r'[（(](亿元|万元|千元|百万元|元)[）)]$',normalized)
    if not match or normalized not in compact(original):
        return None
    name=match.group(1)
    return name,Decimal({'元':'1','千元':'1000','万元':'10000','百万元':'1000000','亿元':'100000000'}[name])


def statement_caption_unit(original: str, title: str):
    """Bind a unit around this caption without crossing another title or amount row."""
    normalized, positions=normalized_positions(original)
    target=normalized_positions(title)[0]
    units=[]
    for match in re.finditer(re.escape(target),normalized):
        start,end=positions[match.start()],positions[match.end()-1]+1
        context=[]
        for lines in (reversed(original[max(0,start-240):start].splitlines()), original[end:end+240].splitlines()):
            for line in lines:
                if physical_caption(line) or compact(line).endswith('表') or row_key(line) in ROW_METRICS:
                    break
                without_dates=re.sub(r'20\d{2}年(?:度)?(?:\d{1,2}(?:[-—至]\d{1,2})?月(?:\d{1,2}日)?)?','',compact(line))
                if re.search(r'\d',without_dates):
                    break
                # Portable text extractors may put the complete data row on one line.
                if any(re.match(re.escape(key)+r'\s*[-(\d]',compact(line)) for key in ROW_METRICS):
                    break
                context.append(line)
        units.append(unit_in('\n'.join(context)))
    unique={value for value in units if value is not None}
    return next(iter(unique)) if len(unique)==1 else None


def table_headings(rows):
    """Compose leading header levels by logical column, including role-only tables."""
    levels=[]
    for row in rows:
        if not row or row_key(row[0]) in ROW_METRICS:
            break
        if any(parse_decimal(value) is not None and not re.fullmatch(r'20\d{2}',compact(value)) for value in row[1:]):
            break
        if not any(re.search(r'20\d{2}|本期|本年|本报告期|期末|年初至|年初到|上期|上年|合并|母公司|公司|项目|科目|附注|比例|比率|原因|说明',compact(value)) for value in row):
            break
        levels.append(row)
    if not levels:
        return [],0
    width=max(map(len,levels))
    return [' '.join(dict.fromkeys(row[column] for row in levels if column<len(row) and row[column]))
            for column in range(width)],len(levels)


def column_scope(heading):
    scopes={'consolidated' if match=='合并' else 'parent' for match in re.findall(r'合并|母公司|公司',compact(heading))}
    return scopes.pop() if len(scopes)==1 else None


def parse_decimal(raw: str) -> Decimal | None:
    return cell_decimal(raw)


def original_has_value(text: str, raw: str) -> bool:
    return original_literal(text,raw.replace('%',''),numeric=True) is not None


def original_row_matches(text: str, row: list[str], headers: list[str]) -> bool:
    """按报表行内数值单元格顺序核对，不能以数字在整页出现为证据。"""
    original,positions=normalized_positions(text)
    label,_=normalized_positions(row_key(row[0]))
    values=[compact(value).replace(',','').replace('%','') for index,value in enumerate(row[1:],1)
        if parse_decimal(value) is not None and (index>=len(headers) or '附注' not in headers[index])]
    for match in re.finditer(re.escape(label),original):
        start=positions[match.end()-1]+1
        segment=text[start:start+max(600,sum(map(len,values))*2+120)]
        cursor=0
        for value in values:
            found=original_literal(segment[cursor:],value,numeric=True)
            if found is None:break
            position=segment.find(found,cursor)
            cursor=position+len(found)
        else:return bool(values)
    return False


def reported_growth_column(headers,row,current_column=None,year=None,period=None):
    if len(row)!=len(headers):return None
    columns=[i for i,h in enumerate(headers) if any(word in h for word in ('增减','同比','增长')) and
        ('%' in h or '%' in row[i]) and (current_column is None or i>current_column)]
    if period=='Q3' and len(columns)>1:
        columns=[i for i in columns if any(marker in headers[i] for marker in ('年初','前三季度','1-9','1—9'))]
    if len(columns)!=1:return None
    column=columns[0]
    if column==len(row)-1:return column
    # 年报比较数可能位于增长率列之后；OCR 的空白跨度不能证明旧数值所在单元格。
    if period=='FY' and year and current_column is not None and all(
        re.fullmatch(rf'{year-2}年(?:度|末)?',compact(h)) for h in headers[column+1:]):return column
    return None


def audit_main_business_notes(pages, reader, report, digest, version, original_texts=None):
    """只接受明确合并或母公司附注中的本期收入列。收入成本附注的主营业务行不能当作总营业收入；续表标题最多沿用下一页。"""
    scope, unit, headers, context, last_page = None, None, [], '', 0
    found, rejected = [], []
    year, period = int(report['year']), report['period']
    for page_number, page in enumerate(pages, 1):
        original = original_texts[page_number-1] if original_texts is not None else reader.pages[page_number - 1].extract_text() or ''
        blocks = ordered_ocr_blocks(page)
        for block in blocks:
            content = block.get('block_content') or ''
            text = compact(content)
            if block.get('block_label') != 'table' or '<table' not in content:
                if len(text)<120 and text in compact(original) and re.search(r'母公司.*(?:附注|注释)', text):
                    scope, unit, headers, context = 'parent', None, [], ''
                elif len(text)<120 and text in compact(original) and re.search(r'合并财务报表.*(?:附注|注释)|合并.*报表项目(?:附注|注释)', text):
                    scope, unit, headers, context = 'consolidated', None, [], ''
                if len(text)<120 and text in compact(original) and re.search(r'营业收入.*营业成本|营业收入和成本', text):
                    context, headers = text, []
                detected = unit_in(content)
                if detected and unit_in(original)==detected: unit = detected
                continue
            if scope is None or not context:
                continue
            if last_page and page_number>last_page+1:
                headers=[]
            rows = grid(content)
            header_rows=[]
            for row in rows:
                if row and not any(parse_decimal(v) is not None for v in row[1:]):
                    header_rows.append(row)
                else: break
            if header_rows:
                width=max(map(len,header_rows))
                merged=[' '.join(dict.fromkeys(row[col] for row in header_rows if col<len(row) and row[col])) for col in range(width)]
                if any('本期' in h or str(year) in h for h in merged): headers=merged
            for ri,row in enumerate(rows):
                if not row or row_key(row[0]) not in {'主营业务','主营业务收入','主营业务收入合计'}:
                    continue
                current=[i for i,h in enumerate(headers) if i>0 and '收入' in h and '成本' not in h
                    and ('本期' in h or str(year) in h) and not any(w in h for w in ('上期','上年','同比','增减'))]
                reason = 'column_unknown' if len(current)!=1 else 'unit_unknown' if not unit else None
                col=current[0] if len(current)==1 else None
                raw=row[col] if col is not None and col<len(row) else ''
                number=parse_decimal(raw)
                if not reason and (number is None or not original_has_value(original,raw) or row_key(row[0]) not in compact(original)):
                    reason='ocr_literal_not_confirmed'
                if reason:
                    rejected.append(dict(page=page_number,row=row[0],metric='main_business_revenue',scope=scope,reason=reason,raw_value=raw))
                    continue
                source=Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,
                    source_path=report['source_path'],page=page_number,table=context,row=row[0],column=headers[col],
                    row_index=ri,column_index=col,raw_value=raw,raw_unit=unit[0],raw_precision=max(0,-number.as_tuple().exponent),
                    literal=' | '.join(row),extraction='ocr',verification='literal_checked')
                key=dict(stock_code=report['stock_code'],year=year,period=period,metric='main_business_revenue',scope=scope,document_version=digest)
                found.append((Fact(id=identity_digest(key),data_version=version,stock_code=report['stock_code'],company=report['company'],
                    year=year,period=period,metric='main_business_revenue',scope=scope,value=format(number*unit[1],'f'),unit='元',status='verified',source=source),
                    unit[1]*Decimal(10)**-source.raw_precision,True))
            last_page=page_number
    return found,rejected


def audit_report(report: dict, version: str, original_cache=None, *, cache_path=None,
                 reference_annotations=True, scope_resolver=None) -> tuple[list[Fact], dict, list[dict]]:
    path = (ROOT / report['source_path']).resolve()
    if not path.is_relative_to((ROOT / 'data_root').resolve()):
        raise ValueError('Source outside registered data root')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = report.get('source_sha256') or report.get('sha256')
    if expected and digest != expected:
        raise ValueError('Original PDF identity changed')
    reader = PdfReader(path)
    if original_cache and digest in original_cache:
        original_texts=original_cache[digest]
        if len(original_texts)!=len(reader.pages):raise ValueError('Accepted original-text cache page identity mismatch')
        original_extractor='accepted_original_text_cache'
    else:
        original_texts,original_extractor=original_text_pages(path,reader)
    cache = str(cache_path) if cache_path is not None else find_json_cache_for_pdf(str(path))
    if not cache:
        raise ValueError('OCR cache missing')
    pages = list(iter_layout_pages(read_ocr_json(cache)))
    cache_digest=hashlib.sha256(Path(cache).read_bytes()).hexdigest()
    if len(pages) != len(reader.pages):
        raise ValueError('OCR/PDF page count mismatch')
    code, year, period = report['stock_code'], int(report.get('year', report.get('report_year'))), report.get('period', report.get('report_period'))
    company = report.get('company') or report.get('stock_abbr', code)
    identity=original_report_identity(dict(stock_code=code,company=company,year=year,period=period),'\n'.join(original_texts[:10]))
    if not identity['company'] or not identity['period']:raise ValueError('Company/report period is not supported by original front matter')
    candidates, rejected, narratives, caption_rejections = [], [], [], []
    statement, headers, unit, last_table_page = None, [], None, 0
    metric_context, metric_context_page = '', 0
    seen_statements = set()
    statements_finished = False
    financial_section, section_unit = False, None
    for page_number, page in enumerate(pages, 1):
        original = original_texts[page_number-1]
        if not statement and metric_context and page_number > max(metric_context_page, last_table_page) + 1:
            metric_context, headers, unit = '', [], None
        from src.etl.canonical_narratives import ocr_narrative
        markdown=ocr_narrative(page)
        if markdown.strip():
            narratives.append(dict(stock_code=code, company=company, year=year, period=period,
                document_version=digest, source_path=report['source_path'], page=page_number, text=markdown,
                text_origin='ocr_markdown_cleaned',ocr_sha256=cache_digest,
                original_text=original,original_text_origin=original_extractor))
        blocks = ordered_ocr_blocks(page)
        for block in blocks:
            content = block.get('block_content') or ''
            label = block.get('block_label') or ''
            if label in {'header', 'footer', 'number'}:
                continue
            is_table = label == 'table' and '<table' in content.lower()
            if not is_table:
                normalized = compact(content)
                caption = physical_caption(content)
                confirmed = original_literal(original,content) is not None
                if caption and confirmed and block.get('caption_context'):
                    owner_status=packed_caption_company_status(path,block['caption_context'],original)
                    # 无法确认身份不等于发现冲突；保留候选，由原件绑定标注待核验。
                    if owner_status=='contradiction':
                        caption_rejections.append(dict(page=page_number,reason='physical_statement_company_'+owner_status,
                                                       context=block['caption_context']))
                        statement, headers, unit, metric_context = None, [], None, ''
                        continue
                if row_key(content)=='财务报表' and confirmed:
                    financial_section, section_unit = True, None
                if caption and caption['family'] in {'资产负债表','利润表','现金流量表'} and confirmed and not statements_finished:
                    proposed = (caption['scope'], caption['family'])
                    if caption['scope']=='mixed':
                        statement, headers = None, []
                        seen_statements.add(proposed)
                        continue
                    continuation_title=proposed==statement and '续' in normalized and page_number<=last_table_page+1
                    if proposed in seen_statements and not continuation_title:
                        statement, headers = None, []
                        continue
                    statement = proposed
                    seen_statements.add(proposed)
                    unit = statement_caption_unit(original,content) or section_unit
                    if not continuation_title:
                        headers, last_table_page = [], 0
                elif confirmed and ((caption and caption['family']=='notes') or re.fullmatch(
                        r'(?:(?:合并|母公司|公司)?(?:财务|会计)?报表(?:主要)?(?:项目)?(?:附注|注释)|(?:合并|母公司|公司)?(?:所有者|股东)权益变动表)',row_key(content))):
                    statement, headers, unit = None, [], None
                    if seen_statements and any(word in normalized for word in ('附注','注释')):
                        statements_finished = True
                        financial_section, section_unit = False, None
                if caption and caption['family']=='indicators' and confirmed:
                    unit = statement_caption_unit(original,content)
                    headers, last_table_page = [], 0
                    metric_context, metric_context_page = normalized, page_number
                # OCR的标题标签也用于表前说明，只有明确章节编号才关闭当前摘要。
                elif confirmed and re.match(r'^(?:[一二三四五六七八九十\d]+[、.．]|[（(][一二三四五六七八九十\d]+[）)])', normalized):
                    metric_context = ''
                detected = unit_in(content)
                if detected and confirmed:
                    if financial_section and not statement and not seen_statements:
                        section_unit = detected
                    if statement or metric_context:
                        unit = detected
                continue
            rows = grid(content)
            if not rows:
                continue
            context = metric_context or ''
            core = not statement and not seen_statements and (bool(context) or any(
                (caption := physical_caption(cell)) and caption['family'] == 'indicators' for cell in rows[0]))
            if core and any(w in context for w in ('变动原因', '发生变动', '分季度', '季度主要')):
                continue
            if not statement and any(any(q in cell for q in ('第一季度', '第二季度', '第三季度', '第四季度')) for row in rows[:3] for cell in row):
                # 年报中的季度摘要有四个单季列，不能沿用年报累计表头。
                continue
            if not statement and not core:
                continue
            if statement and last_table_page and page_number > last_table_page + 1:
                statement, headers = None, []
                if not core:
                    continue
            scope = statement[0] if statement else 'consolidated'
            table_name = (('合并' if scope == 'consolidated' else '母公司') + statement[1]) if statement else '主要会计数据和财务指标'
            continuation=core and last_table_page and page_number<=last_table_page+1 and rows and headers and len(rows[0])==len(headers)
            current_headers = headers.copy() if statement or continuation else []
            explicit_headers, header_count = table_headings(rows)
            if explicit_headers:
                current_headers = explicit_headers
            header_end, header_levels = header_count, rows[:header_count]
            for ri, row in enumerate(rows):
                if not row:
                    continue
                if ri < header_end:
                    continue
                header_identity = row_key(row[0]) in {'项目', '科目', '指标', '附注', '主要会计数据', '主要财务指标'} or (
                    not compact(row[0]) and any(parse_decimal(value) is None and re.search(
                        r'20\d{2}年|本(?:期|年|报告期)|上(?:期|年)|期[初末]|年初|年度|季度|\d{1,2}月',
                        compact(value)) for value in row[1:]))
                if row_key(row[0]) not in ROW_METRICS and header_identity:
                    internal_headers, count = table_headings(rows[ri:])
                    if internal_headers and any(re.search(
                            r'20\d{2}|本(?:期|年|报告期)|上(?:期|年)|期[初末]|年初|年度|季度|\d{1,2}月',
                            compact(heading)) for heading in internal_headers[1:]):
                        # One HTML table can restart its date axis between
                        # annual flows and year-end balances.
                        current_headers = explicit_headers = internal_headers
                        header_end, header_levels = ri + count, rows[ri:ri + count]
                        continue
                if parse_decimal(row[0]) is not None:
                    continue
                metric = ROW_METRICS.get(row_key(row[0]))
                if not metric or metric not in METRICS or scope not in METRICS[metric].scopes:
                    continue
                if statement and PRIMARY_FAMILIES.get(metric) != {'资产负债表':'balance','利润表':'income','现金流量表':'cashflow'}[statement[1]]:
                    continue
                if statement and metric in {'eps_basic', 'eps_diluted', 'roe_weighted', 'roe_weighted_deducted'} and scope == 'parent':
                    continue
                column = select_current_column(current_headers, year, period, metric)
                if core and period == 'Q3':
                    # 三季报摘要的本报告期可能指单季，流量指标必须明确选年初至今累计单元格。
                    flow = metric not in {'total_assets','total_liabilities','total_equity','cash','trading_assets','receivables','inventory','construction','payables','advances','contract_liabilities','short_loans','retained_earnings','debt_ratio','net_assets_per_share'}
                    if flow and column is not None and '年初' not in current_headers[column] and not re.search(r'(1[-—至]9月|前三季度)', current_headers[column]):
                        continue
                if column is not None and len(row) == len(current_headers) - 1 and len(current_headers) > 1 and '附注' in current_headers[1] and column > 1:
                    # OCR 续表可能省略空白附注列，需按原始布局对齐。
                    column -= 1
                    current_headers = [current_headers[0], *current_headers[2:]]
                if column is None or column >= len(row):
                    rejected.append(dict(page=page_number, row=row[0], metric=metric, scope=scope, reason='column_unknown'))
                    continue
                raw = row[column]
                number = parse_decimal(raw)
                if number is None:
                    rejected.append(dict(page=page_number, row=row[0], metric=metric, scope=scope,
                        reason='empty_or_not_disclosed', raw_value=raw))
                    continue
                m = METRICS[metric]
                stated_row_unit=row_unit(row[0],original) if m.dimension=='money' else None
                if stated_row_unit and unit and stated_row_unit!=unit:
                    rejected.append(dict(page=page_number,row=row[0],metric=metric,scope=scope,reason='unit_conflict'))
                    continue
                effective_unit=stated_row_unit or unit
                raw_unit = m.unit if m.dimension != 'money' else (effective_unit[0] if effective_unit else '')
                multiplier = Decimal(1) if m.dimension != 'money' else (effective_unit[1] if effective_unit else None)
                if multiplier is None:
                    rejected.append(dict(page=page_number, row=row[0], metric=metric, scope=scope, reason='unit_unknown'))
                    continue
                if not original_has_value(original, raw) or not original_row_matches(original,row,current_headers):
                    rejected.append(dict(page=page_number, row=row[0], metric=metric, scope=scope,
                        reason='ocr_literal_not_confirmed', raw_value=raw))
                    continue
                normalized = number * multiplier
                raw_precision = max(0, -number.as_tuple().exponent)
                source = Source(document_id='pdf-' + digest, document_version=digest, source_sha256=digest,
                    source_path=report['source_path'], page=page_number, table=table_name, row=row[0],
                    column=current_headers[column], row_index=ri, column_index=column, raw_value=raw,
                    raw_unit=raw_unit, raw_precision=raw_precision, literal=' | '.join(row),
                    extraction='ocr', verification='literal_checked')
                key = dict(stock_code=code, year=year, period=period, metric=metric, scope=scope, document_version=digest)
                candidates.append((Fact(id=identity_digest(key), data_version=version, stock_code=code,
                    company=company, year=year, period=period, metric=metric, scope=scope,
                    value=format(normalized, 'f'), unit=m.unit, status='verified', source=source),
                    multiplier * Decimal(10) ** -raw_precision, bool(statement)))
                if core:
                    # 已披露百分比具有独立的披露身份与比较基准。
                    growth_col = reported_growth_column(current_headers,row,column,year,period)
                    basis=disclosed_rate_basis(current_headers[growth_col],period) if growth_col is not None else None
                    growth_metric = metric + ('_reported_yoy' if basis=='yoy' else '_reported_change_vs_year_end' if basis=='vs_prior_year_end' else '_unknown_rate_basis')
                    if growth_metric in METRICS and growth_col is not None and growth_col < len(row):
                        raw_growth = row[growth_col]
                        growth = parse_decimal(raw_growth)
                        rate_headings=[current_headers[growth_col], *[level[growth_col] for level in reversed(header_levels) if growth_col<len(level)]]
                        heading_original=original
                        if continuation and not explicit_headers and page_number>1:
                            heading_original+='\n'+original_texts[page_number-2]
                        growth_heading=next((heading for heading in rate_headings
                            if disclosed_rate_basis(heading,period)==basis and original_literal(heading_original,heading) is not None),None)
                        unit_proof=(original_literal(original,raw_growth,numeric=True) if '%' in raw_growth else
                                    original_literal(original,growth_heading) if growth_heading and '%' in growth_heading else None)
                        if growth is not None and growth_heading and unit_proof and original_has_value(original, raw_growth):
                            gs = source.model_copy(update=dict(column=growth_heading, column_index=growth_col,
                                raw_value=raw_growth, raw_unit='%', raw_precision=max(0, -growth.as_tuple().exponent)))
                            gkey = {**key, 'metric': growth_metric}
                            candidates.append((Fact(id=identity_digest(gkey), data_version=version, stock_code=code,
                                company=company, year=year, period=period, metric=growth_metric, scope=scope,
                                value=format(growth, 'f'), unit='%', status='verified', source=gs), Decimal('0.01'), False))
            headers = current_headers
            last_table_page = page_number
    if not reference_annotations:
        # 通用多层表头按列绑定合并/母公司，不能将整张表归成同一个口径。
        from src.etl.submission_tables import mixed_statement_candidates
        shared,shared_rejected=mixed_statement_candidates(pages,{**report,'year':year,'period':period,'company':company},
            digest,version,original_texts)
        candidates.extend(shared);rejected.extend(shared_rejected)
    note_facts,note_rejected = audit_main_business_notes(pages, reader,
        {**report,'year':year,'period':period,'company':company}, digest, version, original_texts)
    candidates.extend(note_facts); rejected.extend(note_rejected)
    reviewed,absence=(audited_annotations({**report,'year':year,'period':period,'company':company},digest,version)
                      if reference_annotations else ([],[]))
    candidates.extend(reviewed)
    source_candidates = [item[0].model_dump(mode='json') for item in candidates]
    grouped = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate[0].metric, candidate[0].scope)].append(candidate)
    selected, conflicts = [], []
    for (metric, scope), values in grouped.items():
        # 优先采用最高原始精度与主报表来源；舍入区间须包含精确值。
        values.sort(key=lambda x: (x[1], not x[2], x[0].source.page))
        best = values[0]
        if (scope_resolver is not None and
                any(abs(v[0].decimal - best[0].decimal) > (v[1] + best[1]) / 2 for v in values[1:])):
            # Missing OCR captions can mislabel a parent table as consolidated.
            # Check explicit physical contradictions before comparing amounts;
            # unbound candidates remain and still participate in conflicts.
            checked = []
            for candidate, precision, primary in values:
                resolved, failed = scope_resolver([candidate])
                rejected.extend(failed)
                checked.extend((value, precision, primary) for value in resolved)
            values = checked
            grouped[metric, scope] = values
            if not values:
                continue
            best = values[0]
        if any(abs(v[0].decimal - best[0].decimal) > (v[1] + best[1]) / 2 for v in values[1:]):
            conflicts.append(dict(metric=metric, scope=scope, reason='source_conflict',
                candidates=[v[0].model_dump(mode='json') for v in values]))
            continue
        selected.append(best[0])
    from src.etl.scope_proofs import attach_scope_proofs
    selected,scope_rejected=(scope_resolver(selected) if scope_resolver is not None else attach_scope_proofs(selected))
    source_selection=[]
    if scope_resolver is not None:
        for index,value in enumerate(selected):
            if value.status!='unverified':continue
            choices=grouped[(value.metric,value.scope)]
            # 先完成全候选冲突检查；替代来源须属于同一指标身份且Decimal数值完全相等。
            alternatives=[item[0] for item in choices[1:] if item[0].decimal==value.decimal
                          and item[0].source!=choices[0][0].source]
            if not alternatives:continue
            selection=dict(metric=value.metric,scope=value.scope,
                original_source=choices[0][0].source.model_dump(mode='json'),
                candidates=[item[0].model_dump(mode='json') for item in choices])
            for alternative in alternatives:
                resolved,failed=scope_resolver([alternative])
                scope_rejected.extend(failed)
                bound=next((item for item in resolved if item.status=='verified' and
                    (item.stock_code,item.year,item.period,item.metric,item.scope,item.unit,item.decimal)==
                    (value.stock_code,value.year,value.period,value.metric,value.scope,value.unit,value.decimal)),None)
                if bound is not None:
                    selected[index]=bound
                    break
            selection.update(selected_source=selected[index].source.model_dump(mode='json'),
                reason='exact_value_independently_verified' if selected[index].status=='verified' else 'original_source_unverified')
            source_selection.append(selection)
    rejected.extend(scope_rejected)
    selected = derive_facts(selected, version, rejected=rejected)
    ledger = dict(stock_code=code, company=company, year=year, period=period, document_version=digest,
        source_path=report['source_path'], page_count=len(pages), original_extractor=original_extractor, identity=identity,
        reviewed_cells=len(reviewed),absence_annotations=absence,cache_sha256=hashlib.sha256(Path(cache).read_bytes()).hexdigest(),
        facts=len(selected), verified_metrics=[dict(metric=f.metric, scope=f.scope, id=f.id, status=f.status) for f in selected],
        rejected=rejected, conflicts=conflicts, source_selection=source_selection, caption_rejections=caption_rejections,
        source_candidates=source_candidates,
        missing=[dict(metric=m.id, scope=s, status='unresolved' if any(r['metric'] == m.id and r['scope'] == s and r['reason'] != 'empty_or_not_disclosed' for r in rejected) else 'not_found',
            detail='No verified fact; not_found does not assert non-disclosure') for m in METRICS.values() for s in m.scopes if not any(f.metric == m.id and f.scope == s for f in selected)])
    for annotation in absence:
        if any(f.metric==annotation['metric'] and f.scope==annotation['scope'] for f in selected):raise ValueError('Disclosure/blank source conflict')
        item=next(m for m in ledger['missing'] if m['metric']==annotation['metric'] and m['scope']==annotation['scope'])
        item.update({k:annotation[k] for k in ('status','detail','source')})
    return selected, ledger, narratives


def select_current_column(headers: list[str], year: int, period: str, metric: str) -> int | None:
    matches = []
    for i, header in enumerate(headers[1:], 1):
        h = compact(header)
        if any(w in h for w in ('上年', '上期', '期初', '年初余额', '增减', '同比', '调整前', '附注', '比例', '比率', '变动', '原因', '说明')):
            continue
        if '%' in h or '％' in h:
            if metric not in METRICS or METRICS[metric].dimension!='percent':
                continue
        if re.search(r'年0?1月0?1日', h):
            # 首次执行准则或重述的期初数不能当作年末或季末余额。
            continue
        years = re.findall(r'20\d{2}', h)
        if years and set(years)!={str(year)}:
            continue
        if period == 'Q3' and not any(w in metric for w in ('assets', 'liabilities', 'equity', 'cash', 'inventory', 'receivables', 'payables', 'loans', 'earnings', 'construction')):
            if '本报告期' in h and '年初' not in h:
                continue
        if str(year) in years or any(w in h for w in ('本期', '本年', '本报告期', '期末', '年初至报告期末', '年初到报告期末')):
            matches.append(i)
    if not matches:
        return None
    adjusted = [i for i in matches if '调整后' in headers[i]]
    return (adjusted or matches)[0]


def derive_facts(facts: list[Fact], version: str, *, rejected=None) -> list[Fact]:
    indexed = {(f.metric, f.scope): f for f in facts}
    with localcontext() as context:
        context.prec = 50
        for metric in ('gross_profit', 'gross_margin', 'net_margin', 'debt_ratio'):
            m = METRICS[metric]
            for scope in m.scopes:
                if (metric, scope) in indexed:
                    continue
                cost=indexed.get(('operating_cost',scope))
                if metric in {'gross_profit','gross_margin'} and cost is not None and cost.decimal<0:
                    # A signed expense convention needs an explicit economic rule; keep its literal sign.
                    if rejected is not None:
                        rejected.append(dict(metric=metric,scope=scope,reason='signed_expense_convention_unsupported'))
                    continue
                bases = [indexed.get((i, scope)) for i in m.formula]
                if any(b is None for b in bases):
                    continue
                a, b = bases
                versions = {f.source.document_version if f.source else next(x.source.document_version for x in facts if x.id in f.inputs and x.source) for f in bases}
                if len(versions) != 1 or (metric != 'gross_profit' and b.decimal == 0):
                    continue
                value = a.decimal - b.decimal if metric == 'gross_profit' else a.decimal / b.decimal * 100
                f = Fact(id=identity_digest(dict(inputs=[a.id, b.id], metric=metric)), data_version=version,
                    stock_code=a.stock_code, company=a.company, year=a.year, period=a.period, scope=scope,
                    metric=metric, value=format(value, 'f'), unit=m.unit, status='derived',
                    formula=f'{a.metric} - {b.metric}' if metric == 'gross_profit' else f'{a.metric} / {b.metric} * 100', inputs=[a.id, b.id])
                facts.append(f); indexed[metric, scope] = f
    return facts
