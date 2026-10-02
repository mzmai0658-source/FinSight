"""作品说明：评分工具修正另存记录，保留原题、原预期、原回答和初评分。"""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.real200_acceptance import reference,score,summarize

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--audit',required=True);p.add_argument('--oracle',required=True);a=p.parse_args()
    folder=ROOT/a.run;ref=reference(ROOT/a.audit);oracle=json.loads((ROOT/a.oracle).read_text(encoding='utf-8'))
    assert oracle['data_version']==ref['version']
    records=[];prior={}
    for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line);group=row['case'].get('group')
        if row.get('run'):
            row['original_score']=row['score'];row['score']=score(row['case'],row['run'],ref,oracle,'qwen3.5:9b-q4_K_M',prior.get(group))
            result=row['run']['task'].get('result') or {};facts=result.get('facts') or []
            if group:
                if row['score']['automated_pass'] and facts:prior[group]=facts
                elif result.get('response_kind')=='financial' or any(g['kind'] in {'lookup','compare','rank','chart','quote','cause','sign'} for g in (result.get('request_contract') or {}).get('goals',[])):prior.pop(group,None)
        records.append(row)
    (folder/'rescored_records.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in records)+'\n',encoding='utf-8')
    changes=[{'id':r['case']['id'],'before':r['original_score']['errors'],'after':r['score']['errors']} for r in records if r.get('original_score') and r['score']['errors']!=r['original_score']['errors']]
    (folder/'scoring_corrections.json').write_text(json.dumps({'original_records_sha256':hashlib.sha256((folder/'records.jsonl').read_bytes()).hexdigest(),'scorer_sha256':hashlib.sha256((ROOT/'eval/real200_acceptance.py').read_bytes()).hexdigest(),'question_or_expectation_changes':False,'method_changes':['Comparison conclusion need not display unrequested input amounts; check relation directly','Equivalent calculation goal kinds require same bases, method and result','Unspecified monetary unit uses actual selected unit and independent conversion','Mathematically equivalent margin ratio satisfies definition terminology','Absent accepted version is distinguished from switching versions'],'changes':changes},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summarize(records),ensure_ascii=False))
if __name__=='__main__':main()
