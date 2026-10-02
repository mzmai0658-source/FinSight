"""作品说明：以原始文件字节核对 OCR 单元格，不依赖宽表标签。口径、列位与单位须由明确报表标题绑定；歧义进入审计，不发布为事实。"""
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

ROOT = Path(__file__).resolve().parents[2]


def extractor_digest():
    files=('src/etl/canonical_audit.py','src/etl/reviewed_cells.py','src/agent/v3/catalog.py','src/utils/original_cells.py',
        'src/utils/original_regions.py','src/utils/financial_numbers.py','src/utils/report_identity.py',
        'src/utils/disclosed_rates.py','src/utils/financial_row_identity.py',
        'src/utils/statement_scope.py','src/etl/scope_proofs.py','src/agent/v3/contracts.py',
        'data/reference/v3/statement_scope_proofs.json',
        'data/reference/v3/reviewed_original_cells.json')
    return hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in files)).hexdigest()


def original_text_pages(path: Path, reader) -> tuple[list[str], str]:
    """作品说明：每份原始页只读取一次，保留页界与来源。Poppler 物理布局保留列位置，OCR 缓存不能替代原件验证。"""
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftotext.exe'
    executable=shutil.which('pdftotext') or (str(bundled) if bundled.is_file() else None)
    if executable:
        extracted=subprocess.run([executable,'-layout','-enc','UTF-8',str(path),'-'],capture_output=True,timeout=120,check=True)
        pages=extracted.stdout.decode('utf-8').split('\f')
        if pages and not pages[-1].strip(): pages.pop()
        if len(pages)==len(reader.pages): return pages,'poppler_layout'
    return [page.extract_text() or '' for page in reader.pages],'pypdf'


def compact(value: str) -> str:
    return re.sub(r'\s+', '', value).replace('，', ',').replace('－', '-').replace('−', '-')


def grid(html: str) -> list[list[str]]:
    """作品说明：展开跨行跨列单元格，同时保留逻辑列身份。"""
    result, carried = [], {}
    for ri, row in enumerate(BeautifulSoup(html, 'html.parser').find_all('tr')):
        values, ci = [], 0
        for cell in row.find_all(['td', 'th'], recursive=False):
            while (ri, ci) in carried:
                values.append(carried[ri, ci]); ci += 1
            # 作品说明：OCR HTML 中的字面换行转义只用于恢复布局，不改变数字或符号，也不冒充 PDF 原文。
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


def unit_in(text: str) -> tuple[str, Decimal] | None:
    match = re.search(r'单位\s*[:：]\s*(亿元|万元|千元|百万元|元)', text)
    if not match:
        return None
    unit = match.group(1)
    return unit, Decimal({'元': '1', '千元': '1000', '万元': '10000', '百万元': '1000000', '亿元': '100000000'}[unit])


def row_unit(label: str, original: str) -> tuple[str, Decimal] | None:
    """作品说明：金额后缀只有在完整行名得到原件确认时才构成单位依据。"""
    normalized=compact(label)
    match=re.search(r'[（(](亿元|万元|千元|百万元|元)[）)]$',normalized)
    if not match or normalized not in compact(original):
        return None
    name=match.group(1)
    return name,Decimal({'元':'1','千元':'1000','万元':'10000','百万元':'1000000','亿元':'100000000'}[name])


def statement_caption_unit(original: str, title: str):
    normalized=compact(original)
    positions=[match.end() for match in re.finditer(re.escape(title),normalized)]
    units=[unit_in(normalized[position:position+240]) for position in positions]
    unique={value for value in units if value}
    return next(iter(unique)) if len(unique)==1 else None


def parse_decimal(raw: str) -> Decimal | None:
    return cell_decimal(raw)


def original_has_value(text: str, raw: str) -> bool:
    return original_literal(text,raw.replace('%',''),numeric=True) is not None


def original_row_matches(text: str, row: list[str], headers: list[str]) -> bool:
    """作品说明：按报表行内数值单元格顺序核对，不能以数字在整页出现为证据。"""
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
    # 作品说明：年报比较数可能位于增长率列之后；OCR 的空白跨度不能证明旧数值所在单元格。
    if period=='FY' and year and current_column is not None and all(
        re.fullmatch(rf'{year-2}年(?:度|末)?',compact(h)) for h in headers[column+1:]):return column
    return None


def audit_main_business_notes(pages, reader, report, digest, version, original_texts=None):
    """作品说明：只接受明确合并或母公司附注中的本期收入列。收入成本附注的主营业务行不能当作总营业收入；续表标题最多沿用下一页。"""
    scope, unit, headers, context, last_page = None, None, [], '', 0
    found, rejected = [], []
    year, period = int(report['year']), report['period']
    for page_number, page in enumerate(pages, 1):
        original = original_texts[page_number-1] if original_texts is not None else reader.pages[page_number - 1].extract_text() or ''
        blocks = sorted(page.get('prunedResult', {}).get('parsing_res_list') or [], key=lambda b:(b.get('block_bbox') or [0,0])[1])
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


def audit_report(report: dict, version: str, original_cache=None) -> tuple[list[Fact], dict, list[dict]]:
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
    cache = find_json_cache_for_pdf(str(path))
    if not cache:
        raise ValueError('OCR cache missing')
    pages = list(iter_layout_pages(read_ocr_json(cache)))
    if len(pages) != len(reader.pages):
        raise ValueError('OCR/PDF page count mismatch')
    code, year, period = report['stock_code'], int(report.get('year', report.get('report_year'))), report.get('period', report.get('report_period'))
    company = report.get('company') or report.get('stock_abbr', code)
    identity=original_report_identity(dict(stock_code=code,company=company,year=year,period=period),'\n'.join(original_texts[:10]))
    if not identity['company'] or not identity['period']:raise ValueError('Company/report period is not supported by original front matter')
    candidates, rejected, narratives = [], [], []
    statement, headers, unit, last_table_page = None, [], None, 0
    metric_context = ''
    seen_statements = set()
    statements_finished = False
    for page_number, page in enumerate(pages, 1):
        original = original_texts[page_number-1]
        if original.strip():
            narratives.append(dict(stock_code=code, company=company, year=year, period=period,
                document_version=digest, source_path=report['source_path'], page=page_number, text=original))
        blocks = page.get('prunedResult', {}).get('parsing_res_list') or []
        # 作品说明：依据实际纵坐标确定表格顺序，不按 OCR 标签分组推断。
        blocks = sorted(blocks, key=lambda b: (b.get('block_bbox') or [0, 0])[1])
        for block in blocks:
            content = block.get('block_content') or ''
            label = block.get('block_label') or ''
            if label in {'header', 'footer', 'number'}:
                continue
            is_table = label == 'table' and '<table' in content.lower()
            if not is_table:
                normalized = compact(content)
                title = re.search(r'(合并|母公司)(?:年初到报告期末|年初至报告期末|年初至期末)?(资产负债表|利润表|现金流量表)', normalized)
                if title and len(normalized) < 100 and not statements_finished and title.group(0) in compact(original):
                    proposed = ('consolidated' if title[1] == '合并' else 'parent', title[2])
                    if proposed in seen_statements:
                        statement, headers = None, []
                        continue
                    statement = proposed
                    seen_statements.add(proposed)
                    headers, unit = [], statement_caption_unit(original,title.group(0))
                    last_table_page = 0
                elif re.search(r'(财务报表附注|所有者权益变动表|股东权益变动表)', normalized) and len(normalized) < 100:
                    statement, headers, unit = None, [], None
                    if seen_statements and '附注' in normalized:
                        statements_finished = True
                if any(t in normalized for t in ('主要会计数据', '主要财务指标', '主要财务数据', '主要财务信息')):
                    metric_context = normalized
                if re.match(r'^[一二三四五六七八九十]+、', normalized) and '主要' not in normalized:
                    metric_context = ''
                detected = unit_in(content)
                if detected and unit_in(original)==detected:
                    unit = detected
                continue
            rows = grid(content)
            if not rows:
                continue
            context = metric_context or ''
            core = not statement and not seen_statements and (bool(context) or any('主要会计数据' in cell or '主要财务指标' in cell for cell in rows[0]))
            if core and any(w in context for w in ('变动原因', '发生变动', '分季度', '季度主要')):
                continue
            if not statement and any(any(q in cell for q in ('第一季度', '第二季度', '第三季度', '第四季度')) for row in rows[:3] for cell in row):
                # 作品说明：年报中的季度摘要有四个单季列，不能沿用年报累计表头。
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
            for ri, row in enumerate(rows):
                if not row:
                    continue
                if parse_decimal(row[0]) is not None:
                    continue
                if any(re.search(rf'{year}年|{year}年度|本报告期|本期|期末|年初至报告期末|年初到报告期末', compact(cell)) for cell in row) and row_key(row[0]) not in ROW_METRICS:
                    current_headers = row
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
                    # 作品说明：三季报摘要的本报告期可能指单季，流量指标必须明确选年初至今累计单元格。
                    flow = metric not in {'total_assets','total_liabilities','total_equity','cash','trading_assets','receivables','inventory','construction','payables','advances','contract_liabilities','short_loans','retained_earnings','debt_ratio','net_assets_per_share'}
                    if flow and column is not None and '年初' not in current_headers[column] and not re.search(r'(1[-—至]9月|前三季度)', current_headers[column]):
                        continue
                if column is not None and len(row) == len(current_headers) - 1 and len(current_headers) > 1 and '附注' in current_headers[1] and column > 1:
                    # 作品说明：OCR 续表可能省略空白附注列，需按原始布局对齐。
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
                    # 作品说明：已披露百分比具有独立的披露身份与比较基准。
                    growth_col = reported_growth_column(current_headers,row,column,year,period)
                    basis=disclosed_rate_basis(current_headers[growth_col],period) if growth_col is not None else None
                    growth_metric = metric + ('_reported_yoy' if basis=='yoy' else '_reported_change_vs_year_end' if basis=='vs_prior_year_end' else '_unknown_rate_basis')
                    if growth_metric in METRICS and growth_col is not None and growth_col < len(row):
                        raw_growth = row[growth_col]
                        growth = parse_decimal(raw_growth)
                        unit_proof=original_literal(original,raw_growth,numeric=True) if '%' in raw_growth else original_literal(original,current_headers[growth_col])
                        if growth is not None and unit_proof and original_has_value(original, raw_growth):
                            gs = source.model_copy(update=dict(column=current_headers[growth_col], column_index=growth_col,
                                raw_value=raw_growth, raw_unit='%', raw_precision=max(0, -growth.as_tuple().exponent)))
                            gkey = {**key, 'metric': growth_metric}
                            candidates.append((Fact(id=identity_digest(gkey), data_version=version, stock_code=code,
                                company=company, year=year, period=period, metric=growth_metric, scope=scope,
                                value=format(growth, 'f'), unit='%', status='verified', source=gs), Decimal('0.01'), False))
            headers = current_headers
            last_table_page = page_number
    note_facts,note_rejected = audit_main_business_notes(pages, reader,
        {**report,'year':year,'period':period,'company':company}, digest, version, original_texts)
    candidates.extend(note_facts); rejected.extend(note_rejected)
    reviewed,absence=audited_annotations({**report,'year':year,'period':period,'company':company},digest,version)
    candidates.extend(reviewed)
    grouped = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate[0].metric, candidate[0].scope)].append(candidate)
    selected, conflicts = [], []
    for (metric, scope), values in grouped.items():
        # 作品说明：优先采用最高原始精度与主报表来源；舍入区间须包含精确值。
        values.sort(key=lambda x: (x[1], not x[2], x[0].source.page))
        best = values[0]
        if any(abs(v[0].decimal - best[0].decimal) > (v[1] + best[1]) / 2 for v in values[1:]):
            conflicts.append(dict(metric=metric, scope=scope, reason='source_conflict',
                candidates=[v[0].model_dump(mode='json') for v in values]))
            continue
        selected.append(best[0])
    from src.etl.scope_proofs import attach_scope_proofs
    selected,scope_rejected=attach_scope_proofs(selected)
    rejected.extend(scope_rejected)
    selected = derive_facts(selected, version)
    ledger = dict(stock_code=code, company=company, year=year, period=period, document_version=digest,
        source_path=report['source_path'], page_count=len(pages), original_extractor=original_extractor, identity=identity,
        reviewed_cells=len(reviewed),absence_annotations=absence,cache_sha256=hashlib.sha256(Path(cache).read_bytes()).hexdigest(),
        facts=len(selected), verified_metrics=[dict(metric=f.metric, scope=f.scope, id=f.id, status=f.status) for f in selected],
        rejected=rejected, conflicts=conflicts,
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
        if any(w in h for w in ('上年', '上期', '期初', '年初余额', '增减', '同比', '调整前', '附注')):
            continue
        if re.search(r'年0?1月0?1日', h):
            # 作品说明：首次执行准则或重述的期初数不能当作年末或季末余额。
            continue
        years = re.findall(r'20\d{2}', h)
        if years and str(year) not in years:
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


def derive_facts(facts: list[Fact], version: str) -> list[Fact]:
    indexed = {(f.metric, f.scope): f for f in facts}
    with localcontext() as context:
        context.prec = 50
        for metric in ('gross_profit', 'gross_margin', 'net_margin', 'debt_ratio'):
            m = METRICS[metric]
            for scope in m.scopes:
                if (metric, scope) in indexed:
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
