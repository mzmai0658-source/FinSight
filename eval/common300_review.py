"""复核具体回答与实际原页，复核意见绑定当前回答摘要，不调用问答模型自评。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from eval.common300_run import export
from eval.common300_scoring import summarize
from eval.review_real200 import answer_hash,apply

ROOT=Path(__file__).resolve().parents[1]


def make_cards(folder,source_root):
    from pypdf import PdfReader
    readers={};cards=[]
    for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        if not row['score'].get('review_required'):continue
        result=((row.get('run') or {}).get('task') or {}).get('result') or {};sources=[]
        for evidence in result.get('evidence') or []:
            if evidence.get('fact_id'):continue
            relative=evidence.get('paper_path')
            if not relative:continue
            path=(source_root/relative).resolve();assert path.is_relative_to((source_root/'data_root').resolve())
            sha=hashlib.sha256(path.read_bytes()).hexdigest();assert sha==evidence.get('document_version')
            if sha not in readers:readers[sha]=PdfReader(path)
            page=evidence['page_start'];original=readers[sha].pages[page-1].extract_text() or ''
            assert re.sub(r'\s','',evidence['text']) in re.sub(r'\s','',original),'Published excerpt absent from actual original page'
            sources.append(dict(source_path=relative,sha256=sha,physical_page=page,published_excerpt=evidence['text'],original_page=original))
        cards.append(dict(id=row['case']['id'],question=row['case']['question'],expected=row['case']['expect'],
            answer=(result.get('answer') or {}).get('content'),answer_evidence_sha256=answer_hash(row),
            task_results=result.get('task_results'),sources=sources,automatic_errors=row['score']['errors'],
            preliminary_attribution=row.get('attribution')))
    (folder/'narrative_cards.json').write_text(json.dumps(cards,ensure_ascii=False,indent=2),encoding='utf-8')
    return cards


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--cards',action='store_true')
    p.add_argument('--source-root',default=str(ROOT));p.add_argument('--decisions');p.add_argument('--ids',default='');a=p.parse_args()
    folder=Path(a.run).resolve();assert folder.is_relative_to(ROOT.resolve())
    if a.cards:
        cards=make_cards(folder,Path(a.source_root).resolve())
        if a.ids:
            for card in cards:
                if card['id'] in a.ids.split(','):print(json.dumps(card,ensure_ascii=False))
        else:print(json.dumps(dict(cards=len(cards),path=str(folder/'narrative_cards.json')),ensure_ascii=False))
        return
    assert a.decisions
    input_path=folder/'strict_records.jsonl' if (folder/'strict_records.jsonl').exists() else folder/'records.jsonl'
    rows=[json.loads(line) for line in input_path.read_text(encoding='utf-8').splitlines()]
    decisions=json.loads(Path(a.decisions).read_text(encoding='utf-8'))
    assert all(row['case']['id'] in decisions for row in rows if row['score'].get('review_required')),'Every pending narrative needs a bound independent decision'
    reviewed=apply(rows,decisions)
    for row in reviewed:
        decision=decisions.get(row['case']['id']) or {}
        if supplied:=decision.get('attribution'):
            assert supplied['primary'] in {'model','code','mixed','data','environment','unresolved','none'}
            row.setdefault('attribution',{}).update(supplied,confidence='independent evidence review')
        elif row.get('attribution',{}).get('primary')=='none' and not row['score']['passed']:
            row['attribution'].update(primary='unresolved',reason='Independent narrative veto; requires stage/evidence diagnosis')
    path=folder/'reviewed_records.jsonl';assert not path.exists(),'Do not overwrite a previous review'
    path.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in reviewed)+'\n',encoding='utf-8')
    cases=json.loads((folder/'cases.json').read_text(encoding='utf-8'))['cases']
    if all('goal_expectations' in row['case']['expect'] for row in reviewed):
        summary=export(folder,reviewed,len(cases))
    else:
        from eval.real200_acceptance import summarize as old_summarize
        summary=old_summarize(reviewed,len(cases),len(cases))
        lines=['# 已见120逐题回归（保持原问题、原预期）','']
        exported=[]
        for row in reviewed:
            result=((row.get('run') or {}).get('task') or {}).get('result') or {}
            lines.extend([f'## {row["case"]["id"]} · '+('通过' if row['score']['passed'] else '未通过'),'',row['case']['question'],'',
                (result.get('answer') or {}).get('content') or '没有可展示回答。','',
                '失败项：'+('；'.join(row['score']['errors']) or '无。'),''])
            exported.append(dict(case=row['case'],score=row['score'],narrative_review=row.get('narrative_review'),
                actual={k:result.get(k) for k in ('request_contract','answer','facts','derived_facts','chart_data_list','task_results','outcome','evidence','query_trace','diagnostics','verification_v3')}))
        (folder/'RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
        (folder/'answers.json').write_text(json.dumps(exported,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'reviewed_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(total=summary['total'],passed=summary['passed'],pending=summary['source_review_pending'],critical=summary['critical']),ensure_ascii=False))


if __name__=='__main__':main()
