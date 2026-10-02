"""作品说明：兼容投影仅供数据导入使用，财务字段按明确语义映射。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext

from sqlalchemy import and_, select

from src.agent.v3.catalog import METRICS
from src.agent.v3.contracts import Fact
from src.etl.provenance_store import persist_snapshot
from src.init_db import BalanceSheet, CashFlowSheet, CorePerformanceIndicatorsSheet, IncomeSheet

MODELS = {m.__tablename__:m.__table__ for m in (BalanceSheet, CashFlowSheet, CorePerformanceIndicatorsSheet, IncomeSheet)}
CORE = 'core_performance_indicators_sheet'


def projections(facts: list[Fact], report: dict):
    identity = {'stock_code':report['stock_code'], 'report_year':report['year'], 'report_period':report['period']}
    rows = {name: {**identity, 'stock_abbr':report['company'], **{c.name:None for c in table.columns
        if c.name not in {*identity, 'stock_abbr', 'serial_number', 'created_at', 'updated_at'}}} for name,table in MODELS.items()}
    source_map = {}
    for f in facts:
        if f.scope != 'consolidated' or f.status not in {'verified','derived'}:
            continue
        mappings = list(METRICS[f.metric].legacy)
        reported_mappings={
            'operating_revenue_reported_yoy':[(CORE,'operating_revenue_yoy_growth')],
            'total_operating_revenue_reported_yoy':[('income_sheet','operating_revenue_yoy_growth')],
            'attributable_net_profit_reported_yoy':[(CORE,'net_profit_yoy_growth')],
            'net_profit_reported_yoy':[('income_sheet','net_profit_yoy_growth')],
            'deducted_attributable_net_profit_reported_yoy':[(CORE,'net_profit_excl_non_recurring_yoy')],
            'total_assets_reported_yoy':[('balance_sheet','asset_total_assets_yoy_growth')],
            'total_liabilities_reported_yoy':[('balance_sheet','liability_total_liabilities_yoy_growth')],
            'net_cash_change_reported_yoy':[('cash_flow_sheet','net_cash_flow_yoy_growth')],
        }
        mappings.extend(reported_mappings.get(f.metric,[]))
        if f.metric == 'operating_revenue':
            # 作品说明：迁移后的旧概览字段表示营业收入，利润表同名旧字段仍保持营业总收入身份。
            mappings.append((CORE,'total_operating_revenue'))
        for name, field in mappings:
            if field not in rows[name]:
                continue
            scale = 4 if f.unit in {'%', '元/股'} else 2
            value = f.decimal if f.unit != '元' or field == 'net_cash_flow' else f.decimal / Decimal(10000)
            with localcontext() as context:
                context.prec = 60
                value = value.quantize(Decimal(1).scaleb(-scale),rounding=ROUND_HALF_UP)
            rows[name][field] = value
            s = f.source
            source_map[(name,field)] = dict(canonical_fact_id=f.id, canonical_metric=f.metric,
                value_semantics='direct' if s else 'derived', derivation=f.formula,
                derived_from=f.inputs, statement_scope='combined',
                page_start=s.page if s else None, page_end=s.page if s else None,
                row_name=s.row if s else None, column_name=s.column if s else None,
                table_name=s.table if s else None, raw_value=s.raw_value if s else None,
                unit_multiplier=str((Decimal(1) if field == 'net_cash_flow' else Decimal('0.0001')) *
                    {'元':Decimal(1),'万元':Decimal(10000),'亿元':Decimal(100000000),'千元':Decimal(1000),'百万元':Decimal(1000000)}.get(s.raw_unit,Decimal(1))) if s and f.unit=='元' else '1',
                normalized_value=str(value), storage_precision=scale, extraction_mode='canonical_v3')
    return rows, source_map


def apply_projections(conn, facts: list[Fact], reports: list[dict]):
    """作品说明：兼容投影与现用版本指针在同一事务中发布。"""
    for report in reports:
        scoped = [f for f in facts if (f.stock_code,f.year,f.period)==(report['stock_code'],report['year'],report['period'])]
        rows, sources = projections(scoped,report)
        for name, row in rows.items():
            table = MODELS[name]
            identity = {k:row[k] for k in ('stock_code','report_year','report_period')}
            clause = and_(*(table.c[k]==v for k,v in identity.items()))
            values = {k:v for k,v in row.items() if k not in identity}
            if conn.execute(select(table.c.stock_code).where(clause)).first():
                conn.execute(table.update().where(clause).values(**values))
            else:
                conn.execute(table.insert().values(**row))
        document = dict(document_id='pdf-'+report['document_version'], document_version=report['document_version'],
            source_sha256=report['document_version'], source_path=report['source_path'], page_count=report['page_count'])
        data=dict(stock_code=report['stock_code'], stock_abbr=report['company'], report_year=report['year'], report_period=report['period'],
            _source_document=document, _field_sources={f'{table}.{field}':s for (table,field),s in sources.items()},
            _pre_save_review_status='canonical_verified')
        data.update({f'{table}.{field}':value for table,row in rows.items() for field,value in row.items()})
        persist_snapshot(conn,data,rows,fill_only=False)
