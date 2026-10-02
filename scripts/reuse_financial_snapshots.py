"""作品说明：将已有财务行及来源快照复制到独立现用数据库。"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TABLES=('core_performance_indicators_sheet','balance_sheet','income_sheet','cash_flow_sheet')
IDENTITY=('stock_code','report_year','report_period')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',required=True)
    parser.add_argument('--source-db',default='finsight_real_eval')
    parser.add_argument('--output',default='data/runtime/financial_qa_rollout/reuse.json')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from dotenv import dotenv_values
    os.environ.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    from config.db_config import get_db_config
    from sqlalchemy import create_engine,MetaData,Table,select,and_
    from src.etl.pipeline import _update_company_master
    cfg=get_db_config()
    if cfg.database==args.source_db:raise ValueError('Source and serving databases must differ')
    source=create_engine(replace(cfg,database=args.source_db).connection_string)
    target=create_engine(cfg.connection_string)
    names=(*TABLES,'financial_report_provenance','financial_report_versions')
    src={n:Table(n,MetaData(),autoload_with=source) for n in names}
    dst={n:Table(n,MetaData(),autoload_with=target) for n in names}
    with source.connect() as c:
        original={n:[dict(r) for r in c.execute(select(t)).mappings()] for n,t in src.items()}
    output=ROOT/args.output;output.parent.mkdir(parents=True,exist_ok=True)
    backup=output.with_suffix('.before.json')
    if args.apply and not backup.exists():
        with target.connect() as c:
            saved={n:[dict(r) for r in c.execute(select(t)).mappings()] for n,t in dst.items()}
            company=Table('company',MetaData(),autoload_with=target)
            saved['company']=[dict(r) for r in c.execute(select(company)).mappings()]
        backup.write_text(json.dumps(saved,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    records=[]
    for snapshot in original['financial_report_provenance']:
        identity={k:snapshot[k] for k in IDENTITY};payload=snapshot['payload'];doc=payload.get('document') or {}
        pdf=(ROOT/doc['source_path']).resolve()
        if not pdf.is_relative_to((ROOT/'data_root').resolve()):raise ValueError('Source outside data_root')
        with pdf.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=doc['source_sha256']:raise ValueError('Original PDF changed')
        financial={}
        for name in TABLES:
            rows=[r for r in original[name] if all(r[k]==v for k,v in identity.items())]
            if len(rows)!=1:raise ValueError(f'Incomplete source snapshot: {identity} {name}')
            financial[name]=rows[0]
        checked=0
        for fact in payload.get('facts',[]):
            value=financial.get(fact['table'],{}).get(fact['field'])
            if value is None or abs(float(value)-float(fact['value']))>0.0001:
                raise ValueError(f'Fact does not match stored value: {identity} {fact["field"]}')
            if fact.get('status')=='source_located':
                if fact.get('source_sha256')!=actual or not 1<=fact['page_start']<=fact['page_end']<=doc['page_count']:
                    raise ValueError('Invalid source locator')
                checked+=1
        predicate=and_(*(dst['financial_report_provenance'].c[k]==v for k,v in identity.items()))
        with target.begin() as c:
            existing=c.execute(select(dst['financial_report_provenance'].c.payload).where(predicate)).scalar_one_or_none()
            if existing:
                if (existing.get('document') or {}).get('source_sha256')!=actual:
                    raise ValueError(f'Serving snapshot conflicts: {identity}')
                status='retained_existing'
            elif args.apply:
                for name,row in financial.items():
                    values={k:v for k,v in row.items() if k in dst[name].c and k not in {'serial_number','created_at','updated_at'}}
                    condition=and_(*(dst[name].c[k]==v for k,v in identity.items()))
                    if c.execute(select(dst[name]).where(condition)).first():
                        raise ValueError(f'Serving rows exist without provenance: {identity}')
                    c.execute(dst[name].insert().values(**values))
                c.execute(dst['financial_report_provenance'].insert().values(**snapshot))
                digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                if not c.execute(select(dst['financial_report_versions'].c.version_id).where(dst['financial_report_versions'].c.version_id==digest)).first():
                    c.execute(dst['financial_report_versions'].insert().values(version_id=digest,payload=payload))
                status='copied'
            else:status='ready'
        if args.apply:_update_company_master(identity['stock_code'],financial[TABLES[0]]['stock_abbr'])
        records.append({**identity,'status':status,'source_sha256':actual,'located_facts':checked})
    with source.connect() as c:
        after={n:[dict(r) for r in c.execute(select(t)).mappings()] for n,t in src.items()}
    if after!=original:raise RuntimeError('Source database changed during copy')
    report={'source_database':args.source_db,'target_database':cfg.database,'applied':args.apply,'source_unchanged':True,'records':records}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
