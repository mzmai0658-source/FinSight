"""作品说明：独立用原文检查全部候选或已验收报告的公司、期间身份。"""
import argparse,hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.utils.report_identity import original_report_identity

def check_identity(report,text):
    return original_report_identity(report,text)

def main():
    p=argparse.ArgumentParser();p.add_argument('--audit',default='data/runtime/v3/audit-signed-cached');p.add_argument('--output',default='data/runtime/v3/report-identity.json');a=p.parse_args()
    folder=ROOT/a.audit;receipt=json.loads((folder/'acceptance.json').read_text(encoding='utf-8'))
    for name in ['reports.json','narratives.jsonl']:
        if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=receipt['artifacts'][name]:raise ValueError('Accepted identity artifact changed')
    pages={}
    for line in (folder/'narratives.jsonl').read_text(encoding='utf-8').splitlines():
        item=json.loads(line)
        if item['page']<=10:pages.setdefault(item['document_version'],[]).append((item['page'],item['text']))
    rows=[]
    for report in json.loads((folder/'reports.json').read_text(encoding='utf-8')):
        path=(ROOT/report['source_path']).resolve()
        if not path.is_relative_to((ROOT/'data_root').resolve()):raise ValueError('Report outside data root')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        proof=check_identity(report,'\n'.join(text for _,text in sorted(pages[report['document_version']])))
        proof['file_bytes']=digest==report['document_version']
        rows.append(dict(stock_code=report['stock_code'],company=report['company'],year=report['year'],period=report['period'],
            document_version=digest,checks=proof,passed=all(proof[k] for k in ['company','period','file_bytes'])))
    record=dict(reports=len(rows),passed=sum(row['passed'] for row in rows),results=rows)
    (ROOT/a.output).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(reports=len(rows),passed=record['passed'],unconfirmed=[r for r in rows if not r['passed']]),ensure_ascii=False))

if __name__=='__main__':main()
