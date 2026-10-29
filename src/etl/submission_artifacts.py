"""新原件的处理指纹与文件冻结不引用旧公司逐事实台账。"""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import re

from src.agent.v3.contracts import Fact
from src.agent.v3.repository import identity_digest

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_FILES = (
    'src/etl/submission_service.py','src/etl/submission_artifacts.py',
    'src/etl/submission_sources.py','src/etl/submission_tables.py',
    'src/etl/canonical_audit.py','src/etl/canonical_narratives.py','src/etl/canonical_projection.py',
    'src/agent/v3/catalog.py','src/utils/original_cells.py','src/utils/original_regions.py',
    'src/utils/financial_numbers.py','src/utils/financial_row_identity.py','src/utils/statement_scope.py',
    'src/utils/report_identity.py','src/utils/disclosed_rates.py')


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def pipeline_digest(root=ROOT):
    root=Path(root)
    sources={name:hashlib.sha256((root/name).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()
             for name in PIPELINE_FILES}
    dependencies={}
    for name in ('pydantic','PyMuPDF','pdfplumber','pypdf','beautifulsoup4'):
        try:
            dependencies[name]=package_version(name)
        except PackageNotFoundError:
            raise RuntimeError('Financial extraction dependency is missing: '+name) from None
    return identity_digest(dict(schema=1,sources=sources,fact_schema=Fact.model_json_schema(),dependencies=dependencies))


def freeze_artifacts(paths,root=ROOT):
    root=Path(root).resolve()
    values={}
    for path in paths:
        path=Path(path).resolve()
        if not path.is_relative_to(root/'data/runtime/submissions/assets'):
            raise ValueError('Candidate artifact is outside the approved asset directory')
        values[path.relative_to(root).as_posix()]=file_digest(path)
    return values


def verify_artifacts(hashes,root=ROOT,asset_id=None):
    root=Path(root).resolve()
    base=root/'data/runtime/submissions/assets'
    if asset_id is not None:
        from src.etl.submission_store import valid_id
        base=base/valid_id(asset_id)
    if not isinstance(hashes,dict) or not hashes:
        raise ValueError('Candidate artifacts have not been frozen')
    for rel,expected in hashes.items():
        if not isinstance(rel,str) or '\\' in rel or Path(rel).is_absolute():
            raise ValueError('Invalid candidate artifact path')
        path=(root/rel).resolve()
        if not path.is_relative_to(base) or not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):
            raise ValueError('Candidate artifact path or fingerprint is invalid')
        if not path.is_file() or file_digest(path)!=expected:
            raise ValueError('Candidate artifact changed after extraction')


def validate_facts(facts,reports,*,data_version=None):
    """逐原件重验直接数值和派生链，不凭回答中的通过标记接收事实。"""
    from src.agent.v3.catalog import METRICS
    from src.agent.v3.evidence import checked_source
    values=[value if isinstance(value,Fact) else Fact.model_validate(value) for value in facts]
    ids={value.id:value for value in values}
    if not values or len(ids)!=len(values):
        raise ValueError('Financial facts are empty or duplicated')
    inventory={(report['stock_code'],report['year'],report['period']):report for report in reports}
    if len(inventory)!=len(reports):
        raise ValueError('Original report identity inventory is duplicated')
    unique=set()
    for fact in values:
        report=inventory.get((fact.stock_code,fact.year,fact.period))
        if not report or fact.company!=report['company']:
            raise ValueError('Fact company or report period differs from its original inventory')
        key=(fact.stock_code,fact.year,fact.period,fact.metric,fact.scope)
        metric=METRICS.get(fact.metric)
        if key in unique or not metric or fact.unit!=metric.unit or fact.scope not in metric.scopes or not fact.decimal.is_finite():
            raise ValueError('Fact identity, financial unit or numeric value is invalid')
        unique.add(key)
        if data_version is not None and fact.data_version!=data_version:
            raise ValueError('Fact has a different data version')
        if fact.status=='verified':
            source=fact.source
            if not source or source.source_sha256!=report['document_version'] or source.document_version!=report['document_version'] or source.source_path!=report['source_path'] or not checked_source(fact):
                raise ValueError('Original source validation failed for '+fact.id)
        elif fact.status!='derived':
            raise ValueError('Unverified facts cannot enter a published financial release')
    visited=set()
    def check(fact,parents=()):
        if fact.id in parents or len(parents)>8:
            raise ValueError('Derived fact dependency cycle or excessive depth')
        if fact.id in visited:
            return
        if fact.status=='derived':
            metric=METRICS[fact.metric]
            if len(fact.inputs)!=2 or len(metric.formula)!=2 or any(id not in ids for id in fact.inputs):
                raise ValueError('Derived financial inputs are missing')
            a,b=[ids[id] for id in fact.inputs]
            if (a.metric,b.metric)!=metric.formula:
                raise ValueError('Derived financial formula uses the wrong metrics')
            if any((value.stock_code,value.year,value.period,value.scope,value.data_version)!=
                   (fact.stock_code,fact.year,fact.period,fact.scope,fact.data_version) for value in (a,b)):
                raise ValueError('Derived fact crosses company, period, scope or version')
            check(a,(*parents,fact.id));check(b,(*parents,fact.id))
            with localcontext() as context:
                context.prec=50
                if fact.metric=='gross_profit':
                    expected=a.decimal-b.decimal
                elif fact.metric in {'gross_margin','net_margin','debt_ratio'} and b.decimal!=0:
                    expected=a.decimal/b.decimal*Decimal(100)
                else:
                    raise ValueError('Unsupported or undefined derived financial formula')
            if fact.decimal!=expected:
                raise ValueError('Derived financial value differs from program calculation')
        visited.add(fact.id)
    for value in values:
        check(value)
    if {(fact.stock_code,fact.year,fact.period) for fact in values}!=set(inventory):
        raise ValueError('At least one original report has no verified facts')
    return values
