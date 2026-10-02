"""作品说明：验收规范事实后准备候选版本，并原子发布事实、索引和兼容投影。未解决项保持原状态；不能将未见或被拒绝指标认定为已披露，也不能升级旧核验标记。"""
from __future__ import annotations
import argparse, hashlib, json, sys
from decimal import Decimal, localcontext, ROUND_HALF_UP
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.agent.v3.contracts import Fact
from src.agent.v3.evidence import checked_source
from src.agent.v3.repository import stage_release, publish_release, identity_digest
from src.etl.canonical_audit import extractor_digest,original_row_matches, parse_decimal
from src.etl.canonical_projection import MODELS, projections, apply_projections
from src.etl.release_profiles import check_report_coverage

def validate(folder):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    reports=json.loads((folder/'reports.json').read_text(encoding='utf-8'))
    facts=[Fact.model_validate(f) for f in json.loads((folder/'facts.json').read_text(encoding='utf-8'))]
    original_pages={(r['document_version'],r['page']):r['text'] for r in
        (json.loads(line) for line in (folder/'narratives.jsonl').read_text(encoding='utf-8').splitlines())}
    index=json.loads((folder/'index_coverage.json').read_text(encoding='utf-8'))
    profile=check_report_coverage(manifest,reports)
    assert not manifest['errors'] and not manifest['conflicts']
    assert all(r.get('identity',{}).get('company') and r.get('identity',{}).get('period') for r in reports), 'Original report identity has not been checked'
    if profile.kind=='synthetic':
        from demo.v3.generate import generator_digest
        extractor=generator_digest()
    else:
        extractor=extractor_digest()
    assert manifest['extractor_sha256']==extractor, 'Extraction or its literal-matching dependency changed since this audit'
    assert manifest['version']==identity_digest({'extractor':extractor,'documents':sorted(r['document_version'] for r in reports)}), 'Extractor changed since this audit'
    assert index['version']==manifest['version'] and index['reports']==profile.reports and index['accepted']
    assert len(index['coverage'])==profile.reports and all(r['chunks']>0 for r in index['coverage'])
    assert {(r['stock_code'],r['year'],r['period']) for r in index['coverage']} == {(r['stock_code'],r['year'],r['period']) for r in reports}, 'Index report identities differ'
    ids={f.id:f for f in facts}
    assert len(ids)==len(facts)
    identities=set()
    for number,f in enumerate(facts,1):
        if number%250==0:print(f'Validated {number}/{len(facts)} facts',flush=True)
        assert f.data_version==manifest['version'] and f.status in {'verified','derived'}
        identity=(f.stock_code,f.year,f.period,f.metric,f.scope)
        assert identity not in identities
        identities.add(identity)
        assert f.decimal.is_finite()
        if f.status=='verified':
            assert checked_source(f), f.id
            assert f.source.document_version==f.source.source_sha256
            raw=parse_decimal(f.source.raw_value)
            assert raw is not None
            multiplier={'元':Decimal(1),'千元':Decimal(1000),'万元':Decimal(10000),'百万元':Decimal(1000000),'亿元':Decimal(100000000),'%':Decimal(1),'元/股':Decimal(1)}[f.source.raw_unit]
            assert f.decimal==raw*multiplier, 'Raw cell/unit does not normalize to the fact'
            if not f.source.original_cell:
                row=[cell.strip() for cell in f.source.literal.split('|')]
                assert 0<=f.source.column_index<len(row) and row[f.source.column_index]==f.source.raw_value
                assert original_row_matches(original_pages[(f.source.document_version,f.source.page)],row,['']*len(row)), 'Original cell order mismatch'
                if '_reported_' in f.metric:
                    assert '%' in f.source.raw_value or '%' in f.source.column, 'Reported rate has no percent identity'
                    # 作品说明：年报核心表可能把前两年比较数放在增长率列之后。只接受原件明确显示年份的这种布局，避免强制取末列丢弃合法增长率。
                    if f.source.column_index!=len(row)-1:
                        assert f.period=='FY' and f.source.column_index==len(row)-2, 'Ambiguous reported rate column'
                        text=original_pages[(f.source.document_version,f.source.page)]
                        assert str(f.year-2) in text, 'Trailing comparative year absent from original'
                    heading=f.source.column.replace(' ','')
                    prior_end=any(marker in heading for marker in ('上年度末','上年末','上年年末'))
                    if f.metric.endswith('_reported_change_vs_year_end'):
                        assert f.period!='FY' and prior_end, 'Prior-year-end identity mismatch'
                    elif f.metric.endswith('_reported_yoy'):
                        assert not (f.period!='FY' and prior_end), 'A nonannual year-end change is not YoY'
        else:
            a,b=[ids[i] for i in f.inputs]
            assert (a.stock_code,a.year,a.period,a.scope,a.data_version)==(b.stock_code,b.year,b.period,b.scope,b.data_version)==(f.stock_code,f.year,f.period,f.scope,f.data_version)
            with localcontext() as ctx:
                ctx.prec=50
                expected=a.decimal-b.decimal if f.metric=='gross_profit' else a.decimal/b.decimal*100
            assert f.decimal==expected, f.id
    # 作品说明：采用独立核对的原始 PDF 单元格作为财务验收样例。
    golden={('operating_revenue','parent'):'4896408337.09',('net_profit','parent'):'1393314467.63',
        ('net_profit','consolidated'):'2280337512.47',('attributable_net_profit','consolidated'):'1526274925.98',
        ('operating_revenue','consolidated'):'18597281604.93'}
    golden_code='600085'
    if profile.kind=='synthetic':
        golden_code='990001'
        golden={('operating_revenue','consolidated'):'150000000',('total_operating_revenue','consolidated'):'151000000',
            ('operating_revenue','parent'):'90000000',('net_profit','parent'):'10800000',
            ('net_profit','consolidated'):'19000000',('attributable_net_profit','consolidated'):'18000000',
            ('deducted_attributable_net_profit','consolidated'):'17500000'}
    for key,value in golden.items():
        actual=[f for f in facts if (f.stock_code,f.year,f.period,f.metric,f.scope)==(golden_code,2024,'FY',*key)]
        assert len(actual)==1 and actual[0].decimal==Decimal(value),(key,actual)
    for r in reports:
        selected=[f for f in facts if (f.stock_code,f.year,f.period)==(r['stock_code'],r['year'],r['period'])]
        rows,sources=projections(selected,r)
        attributable=next((f.decimal/10000 for f in selected if f.metric=='attributable_net_profit' and f.scope=='consolidated'),None)
        expected=attributable.quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if attributable is not None else None
        assert rows['core_performance_indicators_sheet']['net_profit_10k_yuan']==expected
        for table,row in rows.items():
            assert not set(row)-set(MODELS[table].c.keys())
            for field,value in row.items():
                if (table,field) in sources:
                    assert Decimal(sources[(table,field)]['normalized_value'])==value
    acceptance=dict(facts_verified=True,reports_verified=True,index_verified=True,projections_verified=True,
        dataset_profile=profile.public_metadata(), included_facts=len(facts),unresolved=sum(len(r['missing']) for r in reports),
        scope='Only included verified cells/derived facts; unresolved metrics are excluded',
        artifacts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder/'facts.json',folder/'reports.json',folder/'index_coverage.json',folder/'narratives.jsonl')})
    acceptance['projection_sha256']=hashlib.sha256((ROOT/'src/etl/canonical_projection.py').read_bytes()).hexdigest()
    return manifest,reports,facts,index,acceptance

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env',default='.env.integration')
    parser.add_argument('--audit',default='data/runtime/v3/audit')
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/args.env,override=True)
    folder=ROOT/args.audit
    manifest,reports,facts,index,acceptance=validate(folder)
    (folder/'acceptance.json').write_text(json.dumps(acceptance,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.publish:
        from sqlalchemy import create_engine,select
        from config.db_config import get_db_config
        engine=create_engine(get_db_config().connection_string)
        backup=ROOT/'data/runtime/v3/backup'
        backup.mkdir(parents=True,exist_ok=True)
        with engine.connect() as conn:
            for name,table in MODELS.items():
                target=backup/(name+'.json')
                if not target.exists():
                    target.write_text(json.dumps([dict(row) for row in conn.execute(select(table)).mappings()],ensure_ascii=False,default=str),encoding='utf-8')
        stage_release(engine,manifest['version'],facts,reports,index['collection'],manifest)
        publish_release(engine,manifest['version'],acceptance,project=lambda conn:apply_projections(conn,facts,reports))
        print(json.dumps(dict(published=manifest['version'],facts=len(facts),reports=len(reports)),ensure_ascii=False))
    else: print(json.dumps(acceptance,ensure_ascii=False))
if __name__=='__main__': main()
