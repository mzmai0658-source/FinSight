"""作品说明：离线只读检查现用版本及完整来源链。"""
import argparse,collections,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine,select
from config.db_config import get_readonly_db_config
from src.agent.v3.contracts import Conditions,Fact,Request
from src.agent.v3.repository import CanonicalRepository,facts,json_payload
from src.agent.v3.executor import ExecutionResult
from src.agent.v3.verification import verify

def main():
    p=argparse.ArgumentParser();p.add_argument('--env',default='.env.integration');p.add_argument('--audit',default='data/runtime/v3/audit-ordered');p.add_argument('--output',default='data/runtime/v3/serving-check.json');a=p.parse_args()
    load_dotenv(ROOT/a.env,override=True)
    engine=create_engine(get_readonly_db_config().connection_string)
    repo=CanonicalRepository(engine)
    with repo._connect() as conn:
        values=[Fact.model_validate(json_payload(v)) for v in conn.execute(select(facts.c.payload).where(facts.c.data_version==repo.version)).scalars()]
    request=Request(turn_id='offline-serving-audit',question='offline full provenance audit',conditions=Conditions(),goals=[])
    numeric=verify(request,ExecutionResult(facts=values),[],[],values).numeric
    # 作品说明：全范围遍历用于离线数值来源检查，不是用户请求，也不证明自然语言理解正确。
    reports=repo.report_catalog();counts=collections.Counter(f.status for f in values)
    folder=ROOT/a.audit
    audit_reports=json.loads((folder/'reports.json').read_text(encoding='utf-8'))
    unresolved=collections.Counter(item['status'] for report in audit_reports for item in report['missing'])
    record=dict(version=3,data_version=repo.version,index_collection=repo.collection,reports=len(reports),companies=len({r['stock_code'] for r in reports}),
        facts=len(values),fact_statuses=dict(counts),numeric_pass=sum(c.status=='pass' for c in numeric),
        numeric_failures=[c.model_dump() for c in numeric if c.status!='pass'],missing_classifications=dict(unresolved),
        audit_sha256=hashlib.sha256((folder/'facts.json').read_bytes()).hexdigest(),
        claim='Numeric provenance of the serving release only; unresolved missing identities and full task acceptance remain open')
    target=ROOT/a.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:record[k] for k in ['data_version','reports','companies','facts','numeric_pass','missing_classifications']},ensure_ascii=False),flush=True)
    return int(bool(record['numeric_failures']))

if __name__=='__main__':sys.exit(main())
