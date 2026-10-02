"""作品说明：独立叙述复核绑定实际回答及原件身份，不以问答模型自评替代验收。"""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def answer_hash(row):
    result=(row.get('run',{}).get('task') or {}).get('result') or {}
    content={'answer':result.get('answer'),'evidence':result.get('evidence'),'task_results':result.get('task_results')}
    return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def cards(folder):
    from pypdf import PdfReader
    output=[];readers={}
    for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        if not row['score'].get('review_required'):continue
        result=(row.get('run',{}).get('task') or {}).get('result') or {};sources=[]
        for evidence in result.get('evidence',[]):
            if evidence.get('fact_id'):continue
            path=(ROOT/evidence['paper_path']).resolve();assert path.is_relative_to((ROOT/'data_root').resolve())
            sha=hashlib.sha256(path.read_bytes()).hexdigest();assert sha==evidence['document_version']
            if sha not in readers:readers[sha]=PdfReader(path)
            page=evidence['page_start'];original=readers[sha].pages[page-1].extract_text() or ''
            normalize=lambda s:''.join(s.split())
            assert normalize(evidence['text']) in normalize(original),'Retrieved excerpt is not in the registered original page'
            sources.append({'source_path':evidence['paper_path'],'document_sha256':sha,'physical_page':page,'published_excerpt':evidence['text'],'original_page':original})
        output.append({'id':row['case']['id'],'question':row['case']['question'],'expected':row['case']['expect'],
                       'answer':(result.get('answer') or {}).get('content'), 'answer_evidence_sha256':answer_hash(row),
                       'task_results':result.get('task_results'),'sources':sources,'automatic_errors':row['score']['errors']})
    path=folder/'narrative_cards.json';path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    return output

def apply(rows,decisions):
    reviewed=[]
    for row in rows:
        row=json.loads(json.dumps(row));decision=decisions.get(row['case']['id'])
        if row['score'].get('review_required') and decision:
            assert decision['answer_evidence_sha256']==answer_hash(row),'Review does not bind this actual answer'
            assert decision['reviewer']=='assistant-independent-review' and decision.get('reason')
            row['narrative_review']=decision;s=row['score'];s['review_status']='pass' if decision['passed'] else 'fail'
            if not decision['passed']:s['errors']=sorted(set([*s['errors'],*decision.get('errors',['narrative_review_failed'])]))
            s['critical']=sorted(set([*s['critical'],*decision.get('critical',[])]))
            s['passed']=s['automated_pass'] and decision['passed'] and not s['critical']
        reviewed.append(row)
    return reviewed

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--cards',action='store_true');p.add_argument('--ids',default='');a=p.parse_args()
    folder=ROOT/a.run
    if a.cards:
        values=cards(folder)
        for card in values:
            if a.ids and card['id'] not in a.ids.split(','):continue
            print(json.dumps(card,ensure_ascii=False))
        return
    from eval.real200_acceptance import summarize
    rows=[json.loads(x) for x in (folder/'rescored_records.jsonl' if (folder/'rescored_records.jsonl').exists() else folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    decisions=json.loads((folder/'narrative_reviews.json').read_text(encoding='utf-8'))
    reviewed=apply(rows,decisions)
    (folder/'reviewed_records.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in reviewed)+'\n',encoding='utf-8')
    summary=summarize(reviewed);(folder/'reviewed_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ['total','passed','source_review_pending','critical','acceptance_passed']},ensure_ascii=False))
if __name__=='__main__':main()
