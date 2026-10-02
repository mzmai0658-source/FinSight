"""作品说明：检查本机 Agent 动作与证据完整性；文字解释仍需审阅。"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentevals.trajectory.match import create_trajectory_match_evaluator


def trajectory(names):
    return [{'role':'assistant','content':'','tool_calls':[
        {'id':str(i),'type':'function','function':{'name':name,'arguments':'{}'}}
        for i,name in enumerate(names)]}]


def score(record):
    result=record.get('result') or {}; expected=record['case']['expected_action']
    events=record.get('events',[]); failures=[]
    tools=[e['data'].get('tool') for e in events if e.get('event')=='tool_call']
    sql=result.get('validation',{}).get('sql_events',[])
    charts=result.get('chart_data_list') or []
    # 作品说明：旧运行没有绘图工具事件，需兼容其实际图表事件。
    if charts and 'render_chart' not in tools: tools.append('render_chart')
    status=result.get('outcome',{}).get('status')
    if record.get('error'): failures.append('transport_error')
    if not result.get('answer',{}).get('content'): failures.append('missing_answer')
    direct=expected in {'help','clarify','unsupported','clarify_or_unsupported'}
    if direct:
        evaluated=create_trajectory_match_evaluator(trajectory_match_mode='strict')(
            outputs=trajectory(tools),reference_outputs=trajectory([]))
        if not evaluated['score']: failures.append('unexpected_tool_call')
        if charts or result.get('facts') or sql: failures.append('unexpected_financial_result')
        allowed={'help':['answered'],'clarify':['needs_clarification'],
                 'unsupported':['unsupported'],'clarify_or_unsupported':['needs_clarification','unsupported']}[expected]
        if status not in allowed: failures.append('wrong_outcome')
        if expected=='help' and result.get('response_kind')!='conversation': failures.append('missing_conversation_kind')
    else:
        if not sql: failures.append('missing_query')
        if expected!='data_chart' and (charts or 'render_chart' in tools): failures.append('unrequested_chart')
        if expected=='data_chart' and not (result.get('query_plan',{}).get('chart') or 'render_chart' in tools or charts):
            failures.append('chart_request_lost')
        if status not in {'answered','partial','no_data'}: failures.append('overrefusal_or_failure')
    fact_index={f['fact_id']:f for f in result.get('facts',[])}
    for chart in charts:
        points=(chart.get('data_source') or {}).get('points',[])
        if len(points)!=len(chart.get('y_data',[])): failures.append('chart_points_unbound'); continue
        for value,point in zip(chart['y_data'],points):
            if value is None:
                if not point.get('missing'): failures.append('unmarked_gap')
                continue
            if point.get('fact_id') not in fact_index: failures.append('unbound_chart_value')
    if sql and all(e.get('status') in {'rejected','error'} for e in sql) and status=='no_data':
        failures.append('failure_reported_as_no_data')
    return {'id':record['case']['id'],'expected_action':expected,'passed':not failures,
            'failures':list(dict.fromkeys(failures)),'outcome':status,'tools':tools,'elapsed':record.get('elapsed')}


def main():
    p=argparse.ArgumentParser(); p.add_argument('run',type=Path); args=p.parse_args()
    records=[json.loads(s) for s in (args.run/'records.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    results=[score(r) for r in records]
    report={'completed':len(results),'passed':sum(r['passed'] for r in results),
            'failures':dict(Counter(f for r in results for f in r['failures'])), 'results':results,
            'limit':'Action/evidence checks only. Exact company/report scope and helpfulness require the separately recorded review; this is not the 95% completion score.'}
    (args.run/'semantic-score.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))

if __name__=='__main__': main()
