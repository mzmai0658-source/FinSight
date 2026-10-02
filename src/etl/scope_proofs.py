"""作品说明：物理坐标标注补充 OCR 的身份依据，不能借此修改原始数字。"""
from functools import lru_cache
import hashlib,json
from pathlib import Path
from src.agent.v3.contracts import StatementScopeProof

ROOT=Path(__file__).resolve().parents[2]
LEDGER=ROOT/'data/reference/v3/statement_scope_proofs.json'


def source_signature(fact):
    value=fact.model_dump(mode='json') if hasattr(fact,'model_dump') else fact
    source={k:v for k,v in value['source'].items() if k not in {'scope_proof','source_path'}}
    identity={k:value[k] for k in ('stock_code','year','period','metric','scope','value','unit')}
    identity['source']=source
    return hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


@lru_cache(maxsize=2)
def _records(path,size,mtime):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    records={row['fact_id']:row for row in payload['records']}
    if len(records)!=len(payload['records']):raise ValueError('Duplicate statement scope annotations')
    return records


def attach_scope_proofs(facts):
    stat=LEDGER.stat()
    records=_records(str(LEDGER),stat.st_size,stat.st_mtime_ns)
    accepted,rejected=[],[]
    for fact in facts:
        row=records.get(fact.id)
        if not fact.source or not row or source_signature(fact)!=row['source_signature']:
            rejected.append(dict(page=fact.source.page if fact.source else None,metric=fact.metric,scope=fact.scope,reason='physical_scope_unverified'))
            continue
        proof=StatementScopeProof.model_validate(row['scope_proof'])
        accepted.append(fact.model_copy(update={'source':fact.source.model_copy(update={'scope_proof':proof})}))
    return accepted,rejected
