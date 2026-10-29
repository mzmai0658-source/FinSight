"""同一原表的日期轴与报表范围轴分别展开，表名不替代列头。"""
from __future__ import annotations

from decimal import Decimal
import re

from src.agent.v3.catalog import METRICS, PRIMARY_FAMILIES
from src.agent.v3.contracts import Fact, Source
from src.agent.v3.repository import identity_digest
from src.utils.financial_row_identity import ROW_METRICS, row_key
from src.utils.statement_scope import physical_caption


def mixed_statement_candidates(pages, report, digest, version, originals):
    from src.etl.canonical_audit import (compact, grid, original_row_matches, parse_decimal, select_current_column,
        unit_in, statement_caption_unit, table_headings, column_scope, ordered_ocr_blocks)
    from src.utils.original_cells import original_literal
    found, rejected = [], []
    title, family, unit, headings, scopes, last_page = None, None, None, [], {}, 0
    year, period = report['year'], report['period']
    for number, page in enumerate(pages, 1):
        original = originals[number - 1]
        for block in ordered_ocr_blocks(page):
            content = block.get('block_content') or ''
            if block.get('block_label') in {'header','footer','number'}:
                continue
            if block.get('block_label') != 'table' or '<table' not in content.lower():
                caption = physical_caption(content)
                if caption:
                    if caption['scope'] == 'mixed' and original_literal(original,content) is not None:
                        title, family = content, caption['family']
                        unit, headings, scopes = statement_caption_unit(original,content), [], {}
                        last_page = number
                    else:
                        title, family = None, None
                elif re.fullmatch(r'(?:财务报表附注|(?:合并|母公司|公司)?(?:所有者|股东)权益变动表)',row_key(content)):
                    title, family = None, None
                detected = unit_in(content)
                if title and detected and original_literal(original,content) is not None:
                    unit = detected
                continue
            if not title or not unit or number - last_page > 1:
                continue
            rows = grid(content)
            explicit_headers, header_count=table_headings(rows)
            if explicit_headers:
                headings=explicit_headers
                scopes={index: scope for index,heading in enumerate(headings)
                        if index and (scope:=column_scope(heading)) is not None}
            for ri, row in enumerate(rows):
                if not row:
                    continue
                if ri<header_count:
                    continue
                metric = ROW_METRICS.get(row_key(row[0]))
                if not metric:
                    continue
                definition = METRICS[metric]
                if PRIMARY_FAMILIES.get(metric) != {'资产负债表':'balance','利润表':'income','现金流量表':'cashflow'}[family]:
                    continue
                if not headings or len(row) != len(headings):
                    rejected.append(dict(page=number,metric=metric,scope='unknown',reason='shared_column_unknown'))
                    continue
                for scope in definition.scopes:
                    candidates = [heading if scopes.get(index)==scope else '' for index,heading in enumerate(headings)]
                    column = select_current_column(candidates, year, period, metric)
                    if column is None:
                        continue
                    raw = row[column]
                    decimal = parse_decimal(raw)
                    if decimal is None:
                        rejected.append(dict(page=number,metric=metric,scope=scope,reason='empty_or_not_disclosed',raw_value=raw))
                        continue
                    if not original_row_matches(original,row,headings):
                        rejected.append(dict(page=number,metric=metric,scope=scope,reason='ocr_literal_not_confirmed'))
                        continue
                    factor = unit[1] if definition.unit=='元' else Decimal(1)
                    source = Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,
                        source_path=report['source_path'],page=number,table=title,row=row[0],column=headings[column],
                        row_index=ri,column_index=column,raw_value=raw,raw_unit=unit[0] if definition.unit=='元' else definition.unit,
                        raw_precision=max(0,-decimal.as_tuple().exponent),literal=' | '.join(row),extraction='ocr',verification='literal_checked')
                    key=dict(stock_code=report['stock_code'],year=year,period=period,metric=metric,scope=scope,document_version=digest)
                    fact=Fact(id=identity_digest(key),data_version=version,stock_code=report['stock_code'],company=report['company'],
                        year=year,period=period,metric=metric,scope=scope,value=format(decimal*factor,'f'),unit=definition.unit,
                        status='verified',source=source)
                    found.append((fact,factor*Decimal(10)**-source.raw_precision,True))
            last_page=number
    return found,rejected
