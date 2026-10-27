"""复用新OCR完整重提取，逐项保存原件核验结果与缺项，不复用旧事实子集。"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal,localcontext
from pathlib import Path

CORE_METRICS=('operating_revenue','attributable_net_profit','deducted_attributable_net_profit',
              'operating_cash_flow','total_assets','eps_basic')


def extract_fresh_document(report,version,cache_path):
    from src.etl.canonical_audit import ROOT,audit_report
    from src.etl.submission_sources import bind_original_source
    from src.agent.v3.evidence import checked_source
    from src.utils.financial_row_identity import checked_row_identity,checked_period_column
    remaining=[]
    def bind(values):
        resolved=[];rejected=[]
        for value in values:
            if not checked_row_identity(value) or not checked_period_column(value):
                rejected.append(dict(id=value.id,metric=value.metric,scope=value.scope,page=value.source.page if value.source else None,
                                     reason='candidate_row_or_period_invalid'))
                continue
            reason='physical_cell_not_bound'
            try:
                diagnostics=[]
                bound=bind_original_source(value,ROOT/report['source_path'],diagnostics=diagnostics)
                contradiction=next((item for item in diagnostics if item.get('classification')=='contradiction'),None)
                if contradiction is not None:
                    rejected.append(dict(id=value.id,metric=value.metric,scope=value.scope,page=value.source.page,
                                         reason=contradiction['reason']))
                    continue
                if bound is not None and checked_source(bound):
                    resolved.append(bound)
                    continue
                if bound is not None:reason='bound_source_check_failed'
            except (ValueError,OSError,IndexError):
                reason='physical_binding_unavailable'
            if value.source is None:
                raise ValueError('Direct OCR candidate has no source')
            copy=value.model_copy(deep=True)
            copy.status='unverified';copy.source.verification='unverified'
            resolved.append(copy)
            remaining.append(dict(id=copy.id,metric=copy.metric,scope=copy.scope,page=copy.source.page,reason=reason))
        return resolved,rejected
    values,ledger,narratives=audit_report(report,version,cache_path=cache_path,
        reference_annotations=False,scope_resolver=bind)
    direct=[value for value in values if value.status!='derived']
    # 同值来源优选后，只保留最终事实仍待核验的绑定原因；原候选及冲突另存来源优选账本。
    pending={value.id:value for value in direct if value.status=='unverified'}
    final_remaining={}
    for item in remaining:
        if item['id'] in pending and item['page']==pending[item['id']].source.page:
            final_remaining.setdefault(item['id'],item)
    ledger['source_binding']=dict(verified=sum(value.status=='verified' for value in direct),
        unverified=sum(value.status=='unverified' for value in direct),remaining=list(final_remaining.values()))
    ledger['reextracted_from_fresh_ocr']=True
    return values,ledger,narratives


def source_reviewed(value,indexed,seen=frozenset()):
    if value.id in seen:return False
    if value.status=='derived':
        return bool(value.inputs) and all(identifier in indexed and source_reviewed(indexed[identifier],indexed,seen|{value.id})
                                          for identifier in value.inputs)
    return value.status=='verified'


def validate_trial_dataset(values,reports,version,*,progress=None):
    """试验发布仍核对真实文件、数值、身份及公式；仅原件定位未完成项可保留未核验。"""
    from src.agent.v3.catalog import METRICS
    from src.agent.v3.evidence import checked_source
    from src.etl.ocr_trial import trial_source_ok
    from src.utils.financial_row_identity import checked_row_identity,checked_period_column
    indexed={value.id:value for value in values}
    inventory={(row['stock_code'],row['year'],row['period']):row for row in reports}
    if len(indexed)!=len(values) or len(inventory)!=len(reports) or not reports:
        raise ValueError('Reextraction inventory is empty or duplicated')
    unique=set();checked=set()
    for position,value in enumerate(values,1):
        if progress and (position==1 or position%200==0):
            progress(dict(stage='validate_original_sources',checked=position-1,total=len(values)))
        key=(value.stock_code,value.year,value.period)
        report=inventory.get(key);metric=METRICS.get(value.metric)
        identity=(*key,value.metric,value.scope)
        if (not report or value.company!=report['company'] or not metric or identity in unique or
            value.data_version!=version or value.scope not in metric.scopes or value.unit!=metric.unit or not value.decimal.is_finite()):
            raise ValueError('Reextracted fact identity, unit or version is invalid')
        unique.add(identity)
        if value.status=='derived':
            if value.source is not None:raise ValueError('Derived fact cannot carry a direct source')
            continue
        if value.inputs or value.formula is not None:raise ValueError('Direct fact cannot carry derived lineage')
        if not checked_row_identity(value) or not checked_period_column(value):
            raise ValueError('Reextracted row or report period is inconsistent')
        source=value.source
        if (source is None or source.source_sha256!=report['document_version'] or source.document_version!=report['document_version']
            or source.source_path!=report['source_path']):
            raise ValueError('Reextracted source differs from original inventory')
        if value.status=='verified':
            if not checked_source(value):raise ValueError('Reextracted verified source failed independent validation')
        elif value.status!='unverified' or not trial_source_ok(value):
            raise ValueError('Reextracted trial candidate is invalid')
    def check(value,parents=frozenset()):
        if value.id in parents or len(parents)>8:raise ValueError('Reextracted formula dependency is cyclic or too deep')
        if value.id in checked:return
        if value.status=='derived':
            metric=METRICS[value.metric]
            if len(value.inputs)!=2 or any(identifier not in indexed for identifier in value.inputs):
                raise ValueError('Reextracted formula inputs are missing')
            a,b=[indexed[identifier] for identifier in value.inputs]
            if (a.metric,b.metric)!=metric.formula:raise ValueError('Reextracted formula metrics differ from catalog')
            identity=(value.stock_code,value.year,value.period,value.scope,value.data_version)
            if any((item.stock_code,item.year,item.period,item.scope,item.data_version)!=identity for item in (a,b)):
                raise ValueError('Reextracted formula crosses conditions')
            check(a,parents|{value.id});check(b,parents|{value.id})
            with localcontext() as context:
                context.prec=50
                if value.metric=='gross_profit':expected=a.decimal-b.decimal
                elif value.metric in {'gross_margin','net_margin','debt_ratio'} and b.decimal!=0:expected=a.decimal/b.decimal*Decimal(100)
                else:raise ValueError('Reextracted formula is unsupported or undefined')
            formula=' - '.join(metric.formula) if value.metric=='gross_profit' else ' / '.join(metric.formula)+' * 100'
            if value.formula!=formula:raise ValueError('Reextracted formula text differs from catalog')
            if value.decimal!=expected:raise ValueError('Reextracted formula result is incorrect')
        checked.add(value.id)
    if progress:progress(dict(stage='validate_formulas',total=len(values)))
    for value in values:check(value)
    if progress:progress(dict(stage='validated',facts=len(values),reports=len(reports)))
    return dict(facts=len(values),reports=len(reports),statuses=dict(Counter(value.status for value in values)))


def _same_staged_fact(left,right,path=()):
    """仅PDF坐标允许一次二进制浮点舍入；财务内容、来源文字和身份仍逐项相等。"""
    from math import isfinite,ulp
    regions={'original_cell':{'row','value','context','heading','scope_context'},
             'scope_proof':{'label_region','value_region','column_scope','caption'}}
    if (len(path)==5 and path[0]=='source' and path[1] in regions
            and path[2] in regions[path[1]] and path[3]=='bbox'):
        return (type(left) in (int,float) and type(right) in (int,float)
                and isfinite(left) and isfinite(right)
                and abs(left-right)<=max(ulp(left),ulp(right)))
    if type(left) is not type(right):return False
    if isinstance(left,dict):
        return left.keys()==right.keys() and all(_same_staged_fact(left[key],right[key],(*path,key)) for key in left)
    if isinstance(left,list):
        return len(left)==len(right) and all(_same_staged_fact(a,b,(*path,i)) for i,(a,b) in enumerate(zip(left,right)))
    return left==right


def validate_staged_snapshot(conn,info,manifest,exports,report_exports,*,progress=None):
    """发布事务内重读实际数据，核对冻结导出、查询列及真实原件证明。"""
    from sqlalchemy import select
    from src.agent.v3.contracts import Fact
    from src.agent.v3.repository import facts,reports,releases,json_payload,identity_digest
    release=conn.execute(select(releases).where(releases.c.version==info['version'])).mappings().one()
    actual_manifest=json_payload(release['manifest'])
    if (release['index_collection']!=info['collection'] or
        {key:value for key,value in actual_manifest.items() if key!='acceptance'}!=
        {key:value for key,value in manifest.items() if key!='acceptance'}):
        raise ValueError('Staged release differs from frozen manifest or index')
    actual=[]
    for row in conn.execute(select(facts).where(facts.c.data_version==info['version']).with_for_update()).mappings():
        value=Fact.model_validate(json_payload(row['payload']))
        if any(row[key]!=getattr(value,key) for key in ('data_version','id','stock_code','year','period','metric','scope','status')):
            raise ValueError('Staged fact query columns differ from payload')
        actual.append(value)
    actual_reports=[]
    for row in conn.execute(select(reports).where(reports.c.data_version==info['version']).with_for_update()).mappings():
        value=json_payload(row['payload'])
        if any(row[key]!=value.get(key) for key in ('stock_code','year','period')):
            raise ValueError('Staged report query columns differ from payload')
        actual_reports.append(value)
    def report_key(value):return value['stock_code'],value['year'],value['period']
    normalized=[value.model_dump(mode='json') for value in actual]
    if (len(actual)!=info['facts'] or len(actual)!=len(exports) or len(actual_reports)!=info['reports'] or
        not all(_same_staged_fact(a,b) for a,b in zip(sorted(normalized,key=lambda value:value['id']),
                                                    sorted(exports,key=lambda value:value['id']))) or
        identity_digest(sorted(actual_reports,key=report_key))!=identity_digest(sorted(report_exports,key=report_key))):
        raise ValueError('Staged contents differ from frozen extraction exports')
    # 逐报告核对同一批完整事实，避免哈希主键顺序反复打开不同PDF并淘汰缓存。
    ordered=sorted(actual,key=lambda value:(value.stock_code,value.year,value.period,value.metric,value.id))
    return validate_trial_dataset(ordered,actual_reports,info['version'],progress=progress)


def field_coverage(values,ledger):
    from src.agent.v3.catalog import METRICS
    indexed={value.id:value for value in values}
    result=[]
    for metric in METRICS.values():
        for scope in metric.scopes:
            matches=[value for value in values if (value.metric,value.scope)==(metric.id,scope)]
            entries=[*ledger.get('rejected',[]),*ledger.get('conflicts',[])]
            matching=[item for item in entries if (item.get('metric'),item.get('scope'))==(metric.id,scope)]
            conflicts=[item for item in matching if item.get('reason')=='source_conflict']
            rejected=[item for item in matching if item.get('reason')!='source_conflict']
            if matches:
                value=matches[0]
                state=value.status if value.status!='derived' else 'derived_verified' if source_reviewed(value,indexed) else 'derived_unverified'
            else:
                absence=next((item for item in ledger.get('absence_annotations',[]) if
                    (item.get('metric'),item.get('scope'))==(metric.id,scope)),None)
                state='conflict' if conflicts else (absence or {}).get('status') or ('unresolved' if rejected else 'not_found')
            result.append(dict(metric=metric.id,scope=scope,status=state,
                               reasons=dict(Counter(item.get('reason','unknown') for item in rejected))))
    return result
