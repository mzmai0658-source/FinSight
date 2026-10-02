"""作品说明：从一份完整运行和独立复核结果生成验收附件，不拼接不同版本的成功回答。"""
from __future__ import annotations
import argparse,hashlib,json,statistics,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.real200_acceptance import summarize

def load(folder):
    path=folder/'reviewed_records.jsonl'
    if not path.exists():path=folder/'records.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()],path

def analysis(rows):
    summary=summarize(rows)
    standalone=[r for r in rows if not r['case'].get('group')]
    dialogues=[r for r in rows if r['case'].get('group')]
    classical=[r for r in standalone if r['case']['category'] in {'查询口径','比较计算','图表','原文出处'}]
    supported=[r for r in rows if r['case']['expect']['kind'] not in {'clarify','unsupported'}]
    summary['segments']={name:{'total':len(values),'passed':sum(r['score']['passed'] for r in values)} for name,values in
                         [('independent_turns',standalone),('dialogue_turns',dialogues),('classical',classical),('supported',supported)]}
    summary['whole_dialogues_passed']=sum(summary['dialogue_groups'].values())
    summary['whole_dialogues_total']=len(summary['dialogue_groups'])
    summary['by_company']={}
    for code in sorted({code for row in rows for code in row['case']['expect'].get('codes',[])}):
        values=[r for r in rows if code in r['case']['expect'].get('codes',[])]
        summary['by_company'][code]={'total':len(values),'passed':sum(r['score']['passed'] for r in values),'overlap':'multi-company cases appear under each requested company; do not sum these denominators'}
    summary['failure_categories']=Counter(error for r in rows if not r['score']['passed'] for error in r['score']['errors'])
    calls=[(r.get('run',{}).get('task',{}).get('result') or {}).get('diagnostics',{}).get('model_calls') for r in rows]
    observed=[n for n in calls if isinstance(n,int)]
    summary['model_calls']={'observed_turns':len(observed),'distribution':Counter(observed),'mean':statistics.mean(observed) if observed else None}
    causes=[r for r in rows if 'cause' in r['case']['expect'].get('goals',[])]
    complete=[];limits=[]
    for row in causes:
        goals=[g for g in ((row.get('run',{}).get('task') or {}).get('result') or {}).get('task_results',[]) if g['kind']=='cause']
        if row['score']['passed'] and goals and all(g['status']=='completed' for g in goals):complete.append(row)
        elif row['score']['passed']:limits.append(row)
    summary['cause_delivery']={'turns':len(causes),'supported_explanation_delivered':len(complete),'reliable_number_with_honest_evidence_limit':len(limits),'failed':len(causes)-len(complete)-len(limits),'note':'Permitted honest evidence gaps may pass the question but do not count as delivered causal explanations'}
    return summary

def cell(value):return str(value).replace('|','\\|').replace('\n',' ')

def report(rows,summary,environment,source):
    lines=['# FinSight真实财报200轮逐题验收','',
           '本附件来自同一个运行目录；独立问题不继承前题状态，对话使用真实保存会话。仅以最终独立评分计算成绩。',
           '复核为助手对照原文、固定预期和程序结果，不表示团队成员已完成人工审核。',
           f'最终评分记录摘要：`{hashlib.sha256(source.read_bytes()).hexdigest()}`；原始运行记录另外保留。',
           f'数据版本：`{environment["data_version"]}`；模型：`{environment["model"]["name"]}`。','',
           f'正确 {summary["passed"]}/{summary["total"]}；待复核 {summary["source_review_pending"]}；严重错误类别 {dict(summary["critical"])}。',
           '','| 类别 | 轮数 | 正确 | 正确率 |','|---|---:|---:|---:|']
    for cat,value in summary['by_category'].items():lines.append(f'| {cat} | {value["total"]} | {value["passed"]} | {value["passed"]/value["total"]:.1%} |')
    lines.extend(['',f'四轮整组通过 {summary["whole_dialogues_passed"]}/{summary["whole_dialogues_total"]}。',
                  '','| 题号 | 类别/组 | 用户原问题 | 独立结论 | 失败项 |','|---|---|---|---|---|'])
    for r in rows:
        c=r['case'];s=r['score'];group=c.get('group') or '独立';label='通过' if s['passed'] else '待复核' if s['review_status']=='pending' else '失败'
        lines.append('| '+' | '.join(cell(v) for v in [c['id'],c['category']+'/'+group,c['question'],label,'；'.join(s['errors'])])+' |')
    lines.extend(['','## 原回答与复核记录',''])
    for row in rows:
        c=row['case'];r=(row.get('run',{}).get('task') or {}).get('result') or {}
        lines.extend([f'### {c["id"]}：{c["question"]}','',
                      '**实际回答**','',(r.get('answer') or {}).get('content') or '本轮没有获得可展示回答。','',
                      '**独立核对**：'+('通过' if row['score']['passed'] else '失败或待复核')+'；'+('；'.join(row['score']['errors']) or '自动项无错误。'),''])
        if row.get('narrative_review'):lines.extend(['**叙述复核**：'+row['narrative_review']['reason'],''])
        if r.get('chart_data_list'):lines.extend(['图表：'+', '.join(x.get('chart_type','unknown') for x in r['chart_data_list'])+'；点值见精确JSON记录。',''])
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--output',default='docs/evidence/real200');a=p.parse_args()
    folder=ROOT/a.run;rows,source=load(folder);summary=analysis(rows);environment=json.loads((folder/'environment.json').read_text(encoding='utf-8'))
    out=ROOT/a.output;out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'RESULTS_200.md').write_text(report(rows,summary,environment,source),encoding='utf-8')
    manifest={'version':3,'data_kind':'real','original_turns':len(rows),'source_run':folder.name,'source_records_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'data_version':environment['data_version'],'index_collection':environment['index_collection'],'model':environment['model'],
              'source_hashes_sha256':hashlib.sha256((folder/'source_hashes.json').read_bytes()).hexdigest(),'suite_sha256':hashlib.sha256((folder/'cases.json').read_bytes()).hexdigest(),
              'reference_date':environment['reference_date'],'scope':'local evaluation documentation; no financial PDF redistribution permission implied'}
    (out/'version.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ['total','passed','accuracy','source_review_pending','critical','acceptance_passed']},ensure_ascii=False))
if __name__=='__main__':main()
