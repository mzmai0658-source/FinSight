"""作品说明：导出完整单版本验收附件，凭据、账号与完整原件页不进入材料包。"""
import argparse,hashlib,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.report_real200 import load,analysis

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--output',default='deliverables/20261002/FinSight-real200-review.zip');a=p.parse_args()
    folder=ROOT/a.run;rows,_=load(folder);summary=analysis(rows)
    assert len(rows)==200 and summary['source_review_pending']==0
    out=ROOT/'docs/evidence/real200';exported=[]
    for row in rows:
        result=(row.get('run',{}).get('task') or {}).get('result') or {}
        exported.append({'case':row['case'],'score':row['score'],'narrative_review':row.get('narrative_review'),
            'actual':{key:result.get(key) for key in ['version','data_version','request_contract','outcome','answer','facts','derived_facts','comparisons','chart_data_list','task_results','query_trace','diagnostics']},
            'seconds':row.get('run',{}).get('seconds'),'model_test_origin':'real Java/SSE and saved state; private user/session authentication omitted'})
    path=out/'records_200.json';path.write_text(json.dumps(exported,ensure_ascii=False,indent=2),encoding='utf-8')
    files=[ROOT/'docs/TECHNICAL_REPORT.md',ROOT/'docs/FinSight-技术报告.pdf',ROOT/'docs/WORK_INTRO.md',ROOT/'docs/THIRD_PARTY.md',ROOT/'docs/REAL200_ACCEPTANCE.md',ROOT/'docs/REAL200_REPAIRS.md',ROOT/'docs/REAL_DATA_RUN.md',ROOT/'docs/DELIVERY_ACCEPTANCE.md',ROOT/'docs/UPLOAD_CHECKLIST.md',ROOT/'eval/real200_cases.json']
    files.extend(p for p in out.rglob('*') if p.is_file() and p.suffix in {'.json','.md','.png'} and 'browser-failure' not in p.name)
    identities={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
    manifest={'kind':'local review materials, not an external publication','reports':'Real financial PDFs and complete original-page cards excluded; source identity and result evidence provided','score':summary['passed'],'total':200,'data_version':exported[0]['actual']['data_version'],'files':identities,
              'raw_records_sha256':hashlib.sha256((folder/'records.jsonl').read_bytes()).hexdigest(),'reviewed_records_sha256':hashlib.sha256((folder/'reviewed_records.jsonl').read_bytes()).hexdigest()}
    destination=(ROOT/a.output).resolve();assert destination.is_relative_to(ROOT.resolve());destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
        for name in identities:z.write(ROOT/name,name)
        z.writestr('REVIEW_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(destination) as z:
        assert z.testzip() is None
        for name,sha in identities.items():assert hashlib.sha256(z.read(name)).hexdigest()==sha
    digest=hashlib.sha256(destination.read_bytes()).hexdigest();destination.with_suffix('.sha256').write_text(digest+'  '+destination.name+'\n',encoding='utf-8')
    print(json.dumps({'zip':str(destination),'bytes':destination.stat().st_size,'files':len(identities),'sha256':digest},ensure_ascii=False))
if __name__=='__main__':main()
