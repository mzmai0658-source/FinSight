"""作品说明：基于固定数据库快照独立评分条件、数值及完成情况，不依赖核验徽标。现有已核对数值作为本轮问答重构的参考；因果解释与歧义对话另行审阅。"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eval.conversation_expectations import expected_for

NUMBER=re.compile(r'(?<![A-Za-z0-9_])([-+]?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|个百分点|元(?:/股)?|[%％])')
REFUSAL=re.compile(r'暂不提供|无法确认|不支持|无法提供|无法按|请明确|请确认|未能|没有可用|无记录|未取得|不执行|不提供|未查询')
PERIOD={'FY':'全年','HY':'上半年','Q1':'一季度','Q3':'前三季度'}
MIXED_PAIRS={'period-6':{(2023,'FY'),(2024,'HY')},
             'calculation-6':{(2023,'FY'),(2024,'HY')}}
# 作品说明：现金流小计存在歧义时，独立明确要求的营业收入仍应保留。
PARTIAL_CLARIFICATION={'metrics-1':{'fields':['total_operating_revenue'],'period':'FY'}}
CATALOG_HEADER=re.compile(r'^\|\s*(?:公司\s*\|\s*股票代码|公司\s*\|\s*年份\s*\|\s*报告期|指标\s*\|\s*单位)\s*\|\s*$',re.MULTILINE)
CAUSE_LIMIT=re.compile(r'无法.{0,12}(?:解释|确认.*原因)|不能.{0,12}(?:解释|推断|确定.*原因)|未找到.{0,24}(?:原文|证据|解释)|证据不足|原因.{0,12}(?:无法|不能|不足|不明)')
PREMISE_LIMIT=re.compile(r'无法.{0,16}(?:是否|有无)?.{0,12}(?:变化|变好|下降|增长|减少|增加|同比)|缺少.{0,20}(?:基期|上年|去年|两期|对比)|(?:单年|一个报告期).{0,24}(?:不能|无法).{0,16}(?:证明|判断|确认)|尚未.{0,12}(?:核实|确认).{0,12}(?:变化|增减|同比)')
COMPARISON_LIMIT=re.compile(r'无法.{0,12}(?:比较|计算|判断.*增减)|不能.{0,12}(?:比较|计算)|两期.{0,16}(?:不全|不足|缺失)|缺少.{0,16}(?:对比|基期|本期)|未计算.{0,16}(?:变化|同比|增减)')
COMPARE_REQUEST=re.compile(r'比一比|比比|对比|比较|相比|(?:跟|和).{0,10}比|(?:哪个|哪家).{0,6}(?:高|低|多|少)|少了多少|多了多少')
PRESENTATION_FOLLOWUP=re.compile(r'^(?:用(?:亿元|万元|元|百分比)|换成(?:亿元|万元|元)|顺便画个图|用(?:柱状图|折线图)|画(?:个|张)?图|把结果列成表|做个表)[，。！!\s]*$')
UP_WORDS=r'增加|增长|上升|提高|提升|多于|高于|更高|上涨'
DOWN_WORDS=r'减少|下降|降低|下滑|回落|低于|更低|少于|负增长'


def finite(value):
    if isinstance(value,bool): return None
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError): return None


def comparison_requested(record):
    question=record['case'].get('question','').strip()
    if COMPARE_REQUEST.search(question): return True
    if not PRESENTATION_FOLLOWUP.fullmatch(question): return False
    for turn in reversed(record.get('history') or []):
        if turn.get('role')!='user': continue
        previous=str(turn.get('content') or '').strip()
        if PRESENTATION_FOLLOWUP.fullmatch(previous): continue
        return bool(COMPARE_REQUEST.search(previous))
    return False


def comparison_conclusion_missing(record,result,expected,answer):
    """作品说明：只有比较对象的全部同口径事实存在时，才能认定显式比较完成。"""
    if not comparison_requested(record) or expected.get('period') not in PERIOD:
        return False
    codes=expected.get('codes') or []
    fields=expected.get('fields') or []
    years=expected.get('years') or []
    if '*' in codes or len(fields)!=1 or not years or len(codes) not in {1,2}:
        return False
    facts={}
    for fact in result.get('facts') or []:
        try:
            key=(str(fact['stock_code']),int(fact['report_year']),str(fact['report_period']),str(fact['field']))
            if key[0] in codes and key[1] in years and key[2]==expected['period'] and key[3]==fields[0]:
                facts[key]=fact
        except (KeyError,TypeError,ValueError): continue
    if len(facts)!=len(codes)*len(years):
        return False
    prose='\n'.join(line for line in answer.splitlines() if not line.lstrip().startswith('|'))
    if len(codes)==1 and len(years)>=2:
        ordered=[facts[(codes[0],year,expected['period'],fields[0])] for year in sorted(years)]
        first,last=finite(ordered[0].get('value')),finite(ordered[-1].get('value'))
        if first is None or last is None: return False
        if result.get('derived_facts') and NUMBER.search(answer): return False
        if math.isclose(first,last,rel_tol=1e-10,abs_tol=1e-8):
            return not bool(re.search(r'持平|相同|一致|不变|没有变化',prose))
        direction=UP_WORDS if last>first else DOWN_WORDS
        return not bool(re.search(direction,prose))
    if len(codes)==2:
        winners=[]
        for year in years:
            pair=[facts[(code,year,expected['period'],fields[0])] for code in codes]
            values=[finite(fact.get('value')) for fact in pair]
            if None in values: return False
            if math.isclose(values[0],values[1],rel_tol=1e-10,abs_tol=1e-8):
                winners.append((year,None,None))
            else:
                high=pair[0] if values[0]>values[1] else pair[1]
                low=pair[1] if values[0]>values[1] else pair[0]
                winners.append((year,str(high.get('stock_abbr') or high['stock_code']),
                                str(low.get('stock_abbr') or low['stock_code'])))
        if all(winner is None for _,winner,_ in winners):
            return not bool(re.search(r'持平|相同|一致|一样',prose))
        if len(years)>1 and len({winner for _,winner,_ in winners})==1:
            winner=winners[0][1]
            high_phrase=r'(?:更高|高于|超过|多于|比.{0,12}高)'
            if winner and re.search(r'(?:均|都|各年|两年|每年).{0,30}'+re.escape(winner)+r'.{0,18}'+high_phrase+'|'+re.escape(winner)+r'.{0,18}(?:均|都).{0,18}'+high_phrase,prose):
                return False
        sentences=re.split(r'[。；;\n]',prose)
        for year,winner,loser in winners:
            if winner is None:
                matched=any((len(years)==1 or str(year) in sentence) and re.search(r'持平|相同|一致|一样',sentence) for sentence in sentences)
            else:
                winner_high=re.compile(re.escape(winner)+r'.{0,18}(?:更高|高于|超过|多于|比.{0,12}高)')
                loser_low=re.compile(re.escape(loser)+r'.{0,18}(?:更低|低于|少于|比.{0,12}低)')
                matched=any((len(years)==1 or str(year) in sentence) and (winner_high.search(sentence) or loser_low.search(sentence)) for sentence in sentences)
            if not matched: return True
        return False
    return False


def score(record,snapshot):
    case=record['case']; result=record.get('result',{}); expected=dict(expected_for(case))
    partial_clarification=PARTIAL_CLARIFICATION.get(case['id'])
    if partial_clarification: expected.update(partial_clarification)
    answer=(result.get('answer') or {}).get('content','')
    failures=[]; warnings=[]
    plan=result.get('query_plan') or {}
    outcome=(result.get('outcome') or {}).get('status')
    if record.get('error'): failures.append('transport_or_terminal_error')
    if not answer.strip(): failures.append('empty_answer')
    if re.search(r'\\n\s*\d+[.、]',answer): failures.append('literal_newline')
    if re.search(r'[（(]\s*(?:数据|待补充|数值)[）)]',answer): failures.append('placeholder_answer')
    evidence=result.get('evidence') or []
    sql=[e for e in evidence if e.get('type')=='sql']
    successful=[e for e in sql if e.get('status')=='success' and e.get('rows')]
    if sql and not successful:
        if re.search(r'(?:目前|已经|库中|收录|可查|最新).{0,16}20\d{2}',answer) and not REFUSAL.search(answer):
            failures.append('unsupported_coverage_assertion')
        if NUMBER.search(answer): failures.append('numeric_answer_without_sql_rows')
    if sql and all(e.get('status') in {'error','rejected'} for e in sql):
        if re.search(r'未入库|未收录|未披露|数据库.{0,8}没有',answer): failures.append('failure_mislabeled_as_absence')
    if expected.get('period') in {'unsupported','clarify'}:
        requested=expected['period']
        if NUMBER.search(answer): failures.append('answer_to_ambiguous_or_unsupported_request')
        if requested=='clarify' and not (result.get('needs_clarification') or result.get('outcome',{}).get('status')=='needs_clarification'):
            failures.append('missing_clarification')
        if outcome and outcome != {'unsupported':'unsupported','clarify':'needs_clarification'}[requested]:
            failures.append('wrong_result_status')
    if partial_clarification:
        if outcome and outcome!='partial': failures.append('partial_clarification_wrong_status')
        options=(result.get('clarify_options') or plan.get('options') or [])
        if not (result.get('needs_clarification') or options or re.search(r'哪一种现金流|请(?:明确|确认).{0,8}现金流',answer)):
            failures.append('partial_clarification_missing_question')
        cash_fields={'net_cash_flow','operating_cf_net_amount','investing_cf_net_amount','financing_cf_net_amount'}
        if (any(f.get('field') in cash_fields for f in result.get('facts') or [])
                or re.search(r'现金流[^\n|]{0,24}\|?\s*[-+]?\d[\d,.]*\s*(?:亿元|万元|元)',answer)):
            failures.append('ambiguous_cash_flow_guessed')
    if plan and expected.get('period') not in {'unsupported','clarify'}:
        if expected.get('codes') and expected['codes']!=['*'] and set(plan.get('codes') or [])!=set(expected['codes']):
            failures.append('planned_wrong_company')
        if expected.get('years') and set(int(pair[0]) for pair in plan.get('pairs') or [])!=set(expected['years']):
            failures.append('planned_wrong_year')
        if expected.get('fields') and not set(expected['fields']).issubset(set(plan.get('metrics') or [])):
            failures.append('planned_missing_metric')
        if (expected.get('period') in PERIOD and plan.get('pairs')
                and plan.get('intent')!='coverage_periods'
                and any(pair[1]!=expected['period'] for pair in plan['pairs'])):
            failures.append('planned_wrong_report_period')
        required_pairs=MIXED_PAIRS.get(case['id'])
        if required_pairs and set((int(year),period) for year,period in plan.get('pairs') or [])!=required_pairs:
            failures.append('planned_wrong_year_period_pair')
    asks_for_cause=bool(re.search(r'因为|导致|原因|为啥|为什么|怎么解释|怎么说明',case.get('question','')))
    if asks_for_cause:
        if plan and not plan.get('needs_evidence'):
            failures.append('causal_question_not_planned')
        if not result.get('answer',{}).get('references') and not CAUSE_LIMIT.search(answer):
            failures.append('causal_answer_without_evidence_or_limitation')
        if not (CAUSE_LIMIT.search(answer) or re.search(r'因为|由于|主要原因|原因是|导致|受到.{0,12}影响',answer)):
            failures.append('causal_question_not_addressed')
        if re.search(r'少了|增加了?|下降|上升|变[了好差]|增长',case.get('question','')):
            facts=result.get('facts') or []
            years={f.get('report_year') for f in facts}
            comparison_made=bool(result.get('derived_facts')) or (len(years)>1 and bool(re.search(r'同比|相比|较上年|增长|下降|减少|增加',answer)))
            if not comparison_made and not PREMISE_LIMIT.search(answer):
                failures.append('change_premise_unchecked')
    # 作品说明：独立按固定数据行身份和字段检查返回事实。
    snapshot_index={}
    for table,rows in snapshot['tables'].items():
        for row in rows:
            for field,value in row.items():
                if field.startswith('_') or finite(value) is None: continue
                key=(str(row['stock_code']),int(row['report_year']),str(row['report_period']),field)
                snapshot_index.setdefault(key,set()).add(finite(value))
    actual_facts=result.get('facts') or []
    if expected.get('fields') and expected.get('period') not in {'unsupported','clarify'}:
        if not actual_facts and (str(plan.get('intent','')).startswith('coverage_') or CATALOG_HEADER.search(answer)):
            failures.append('fact_request_answered_as_catalog')
    required_pairs=MIXED_PAIRS.get(case['id'])
    for fact in actual_facts:
        try: key=(str(fact['stock_code']),int(fact['report_year']),str(fact['report_period']),str(fact['field']))
        except (KeyError,ValueError,TypeError): failures.append('fact_identity_missing'); continue
        values=snapshot_index.get(key,set())
        if not any(math.isclose(float(fact['value']),v,rel_tol=1e-10,abs_tol=1e-6) for v in values):
            failures.append('fact_disagrees_with_snapshot')
        if expected.get('codes') and expected['codes']!=['*'] and key[0] not in expected['codes']: failures.append('wrong_company')
        if expected.get('years') and key[1] not in expected['years']: failures.append('wrong_year')
        if expected.get('period') in PERIOD and key[2]!=expected['period']: failures.append('wrong_report_period')
        if required_pairs and (key[1],key[2]) not in required_pairs:
            failures.append('wrong_year_period_pair')
    required=[]
    if expected.get('fields') and expected.get('years') and (expected.get('period') in PERIOD or required_pairs):
        codes=expected['codes'] if expected['codes']!=['*'] else sorted({key[0] for key in snapshot_index})
        for code in codes:
            pairs=required_pairs or {(year,expected['period']) for year in expected['years']}
            for year,period in pairs:
                for field in expected['fields']:
                    key=(code,year,period,field)
                    if key in snapshot_index: required.append((key,snapshot_index[key]))
        claims=[]
        for match in NUMBER.finditer(answer):
            value=float(match.group(1).replace(',','')); unit=match.group(2)
            claims.append((value,unit))
        for key,values in required:
            field=key[3]
            def equals(value,unit):
                normalized=value*10000 if unit=='亿元' else value/10000 if unit=='元' and field!='eps' else value
                # 作品说明：以亿元展示四位小数时，对应万元值的最大舍入误差为 0.5。
                tolerance=0.500001 if unit=='亿元' else 0.000051 if unit=='元' and field!='eps' else .0051
                return any(math.isclose(normalized,v,rel_tol=1e-10,abs_tol=tolerance) for v in values)
            if not any(equals(v,u) for v,u in claims): failures.append('available_requested_value_not_presented')
        if required and outcome in {'no_data','query_failed','needs_clarification','unsupported'}:
            failures.append('available_request_refused')
        if not required and outcome=='answered' and not actual_facts:
            failures.append('answered_without_available_fact')
        if len(required)>1 and not re.search(r'\|.+\|\s*\n\|\s*[-:]',answer) and not result.get('chart_data_list'):
            failures.append('multiple_values_without_table_or_chart')
    if partial_clarification and required and 'available_requested_value_not_presented' in failures:
        failures.append('partial_clarification_omits_known_value')
    if expected.get('fields') and len(expected.get('years') or [])>1 and outcome not in {'query_failed','unsupported','needs_clarification'}:
        chart_years={str(year) for chart in result.get('chart_data_list') or [] for year in chart.get('x_data') or []}
        if any(str(year) not in answer and str(year) not in chart_years for year in expected['years']):
            failures.append('requested_year_not_disclosed')
    if (case.get('category')=='comparison' and len(expected.get('codes') or [])==1
            and len(expected.get('years') or [])>1 and len(expected.get('fields') or [])==1
            and expected.get('period') in PERIOD):
        expected_slots=len(expected['years'])
        if 0<len(required)<expected_slots and not COMPARISON_LIMIT.search(answer):
            failures.append('incomplete_comparison_not_explained')
    if re.search(r'一样吗|相同吗',case.get('question','')) and len(required)>=2:
        if not re.search(r'不一样|不同|不相同|相同|一致|一样',answer):
            failures.append('equality_question_not_answered')
    if comparison_conclusion_missing(record,result,expected,answer):
        failures.append('explicit_comparison_lacks_conclusion')
    charts=result.get('chart_data_list') or []
    fact_index={f.get('fact_id'):f for f in actual_facts}
    for derived in result.get('derived_facts') or []:
        input_ids=derived.get('source_fact_ids') or []
        if len(input_ids)!=2 or any(item not in fact_index for item in input_ids):
            failures.append('derived_inputs_unavailable'); continue
        base,current=(fact_index[item] for item in input_ids)
        if any(base.get(k)!=current.get(k) for k in ('stock_code','report_period','field')):
            failures.append('derived_scope_mismatch'); continue
        if derived.get('calculation')=='yoy' and (base.get('unit')=='%' or current.get('unit')=='%'):
            failures.append('percentage_rate_compounded')
        try:
            start,end=float(base['value']),float(current['value'])
            expected_value=((end-start)/abs(start)*100 if derived.get('calculation')=='yoy' and start!=0
                            else end-start if derived.get('calculation') in {'difference','percentage_points'}
                            else None)
            if expected_value is None or not math.isclose(float(derived['value']),expected_value,rel_tol=1e-8,abs_tol=1e-6):
                failures.append('derived_formula_mismatch')
        except (TypeError,ValueError,ZeroDivisionError): failures.append('derived_formula_mismatch')
    for chart in charts:
        source=chart.get('data_source') or {}; points=source.get('points') or []
        if len(points)!=len(chart.get('y_data') or []): failures.append('chart_point_without_fact')
        for point,value in zip(points,chart.get('y_data') or []):
            if value is None:
                if not point.get('missing') or point.get('fact_id') or point.get('row_id'):
                    failures.append('chart_gap_mislabeled')
                continue
            fact=fact_index.get(point.get('fact_id'))
            if not fact: failures.append('chart_fact_unavailable'); continue
            scale=.0001 if source.get('unit')=='亿元' and fact.get('unit')=='万元' else 10000 if source.get('unit')=='元' and fact.get('unit')=='万元' else 1
            if not math.isclose(float(value),float(fact['value'])*scale,rel_tol=1e-8,abs_tol=1e-6): failures.append('chart_value_mismatch')
        if chart.get('chart_type')=='line':
            try:
                years=sorted(int(x) for x in chart.get('x_data') or [])
                if years and years!=list(range(years[0],years[-1]+1)):
                    failures.append('chart_bridges_missing_year')
            except (TypeError,ValueError): failures.append('chart_invalid_year_axis')
    if case['category'] in {'dialog','holdout','evidence','coverage'}:
        warnings.append('semantic_review_required')
    return {'id':case['id'],'category':case['category'],'failures':sorted(set(failures)),
            'review':warnings,'automatic_pass':not failures,'available_value_case':bool(required),
            'elapsed':record.get('elapsed'), 'outcome':result.get('outcome',{}).get('status','legacy')}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('run',type=Path)
    args=parser.parse_args()
    snapshot=json.loads((args.run/'snapshot.json').read_text(encoding='utf-8'))
    records=[json.loads(line) for line in (args.run/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    results=[score(record,snapshot) for record in records]
    counts=Counter(f for row in results for f in row['failures'])
    report={'completed':len(results),'automatic_pass':sum(r['automatic_pass'] for r in results),
            'review_required':sum(bool(r['review']) for r in results),'failure_categories':dict(counts),
            'scorer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'results':results,'scoring_limit':'QA scoring against frozen database values and requested scopes; narrative and conversational review remains separate.'}
    (args.run/'score.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
