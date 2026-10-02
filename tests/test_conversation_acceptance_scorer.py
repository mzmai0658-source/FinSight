"""作品说明：独立评分器应识别看似合理却错误的回答，不能相信系统徽标。"""
import json
import sys

import pytest

from eval.score_conversation_acceptance import score
from eval.conversation_cases import cases
from eval.conversation_expectations import expected_for
from eval.report_conversation_acceptance import main as report_main

def test_fixed_case_counts_and_all_dialog_expectations_exist():
    dev=cases(); held=cases('holdout')
    assert len(dev)==120 and len(held)==30
    assert len({c['group'] for c in dev if c['category']=='dialog'})==12
    assert all(expected_for(c) for c in dev if c['category']=='dialog')
    assert all(expected_for(c) for c in held)

def record(answer,status='pass'):
    return {'case':{'id':'x','category':'time','expected':{'codes':['002082'],'years':[2024],'fields':['total_operating_revenue'],'period':'FY'}},
            'result':{'answer':{'content':answer},'verification':{'status':status}}}

SNAPSHOT={'tables':{'income_sheet':[{'stock_code':'002082','stock_abbr':'万邦德','report_year':2024,'report_period':'FY','total_operating_revenue':100}]}}

def test_pass_badge_does_not_rescue_wrong_value_or_refusal():
    assert 'available_requested_value_not_presented' in score(record('万邦德2024年营业收入999万元。'),SNAPSHOT)['failures']
    assert 'available_requested_value_not_presented' in score(record('暂不提供数据。'),SNAPSHOT)['failures']

def test_placeholder_and_literal_newline_are_failures():
    failure=score(record(r'| 年份 | 营收 |\n1. （数据）'),SNAPSHOT)['failures']
    assert 'placeholder_answer' in failure and 'literal_newline' in failure

def test_failed_query_is_not_evidence_for_missing_data():
    r=record('数据库未收录这个年份。')
    r['result']['evidence']=[{'type':'sql','status':'rejected','rows':[]}]
    assert 'failure_mislabeled_as_absence' in score(r,SNAPSHOT)['failures']

def test_structured_plan_checks_scope_even_when_no_facts_are_returned():
    r=record('当前查询范围内无记录。')
    r['result']['query_plan']={'codes':['002821'],'pairs':[[2023,'HY']], 'metrics':['net_profit']}
    r['result']['outcome']={'status':'no_data'}
    failures=score(r,SNAPSHOT)['failures']
    assert {'planned_wrong_company','planned_wrong_year','planned_missing_metric',
            'planned_wrong_report_period','available_request_refused'} <= set(failures)

def test_unsupported_named_company_is_not_scored_as_correct_clarification():
    r={'case':{'id':'unsupported-check','category':'company',
               'expected':{'codes':[],'years':[],'fields':[],'period':'unsupported'}},
       'result':{'answer':{'content':'请补充公司名称。'},'outcome':{'status':'needs_clarification'}}}
    assert 'wrong_result_status' in score(r,SNAPSHOT)['failures']

def test_four_decimal_billion_yuan_display_allows_half_wan_rounding():
    snapshot={'tables':{'income_sheet':[{'stock_code':'002082','stock_abbr':'万邦德',
        'report_year':2024,'report_period':'FY','total_operating_revenue':144336.52}]}}
    r=record('万邦德2024年营业收入为14.4337亿元。')
    assert 'available_requested_value_not_presented' not in score(r,snapshot)['failures']

def test_causal_question_cannot_be_silently_reduced_to_a_number():
    r=record('万邦德2024年营业收入100万元。')
    r['case']['question']='万邦德24年收入少了，是因为疫情吗？别猜'
    r['result']['query_plan']={'codes':['002082'],'pairs':[[2024,'FY']],
                                'metrics':['total_operating_revenue'],'needs_evidence':False}
    assert 'causal_question_not_planned' in score(r,SNAPSHOT)['failures']

def test_partial_clarification_keeps_the_unambiguous_revenue():
    snapshot={'tables':{'income_sheet':[{'stock_code':'600080','stock_abbr':'金花股份',
        'report_year':2022,'report_period':'FY','total_operating_revenue':100}]}}
    r={'case':{'id':'metrics-1','category':'metrics','question':'金花股份22年收入和现金流分别多少',
               'expected':{'codes':['600080'],'years':[2022],'fields':[],'period':'clarify'}},
       'result':{'answer':{'content':'金花股份2022年营业收入100万元。现金流请明确哪一种。'},
                 'query_plan':{'codes':['600080'],'pairs':[[2022,'FY']],
                               'metrics':['total_operating_revenue','net_cash_flow'],
                               'options':['经营活动','投资活动','筹资活动']},
                 'outcome':{'status':'partial'},'needs_clarification':True,
                 'facts':[{'stock_code':'600080','report_year':2022,'report_period':'FY',
                           'field':'total_operating_revenue','value':100}]}}
    assert score(r,snapshot)['failures']==[]
    r['result']['answer']['content']='现金流请明确哪一种。'
    assert 'partial_clarification_omits_known_value' in score(r,snapshot)['failures']
    r['result']['outcome']['status']='needs_clarification'
    assert 'partial_clarification_wrong_status' in score(r,snapshot)['failures']
    r['result']['answer']['content']='| 指标 | 数值 |\n|---|---|\n| 收入 | 100万元 |\n| 现金流 | 42元 |'
    assert 'ambiguous_cash_flow_guessed' in score(r,snapshot)['failures']

def test_requested_financial_fact_cannot_be_replaced_by_catalog():
    r={'case':{'id':'dialog-5-3','category':'dialog','question':'看最新三年的收入'},
       'result':{'answer':{'content':'| 公司 | 年份 | 报告期 |\n|---|---|---|\n| 万邦德 | 2024 | 全年 |'},
                 'query_plan':{'intent':'coverage_periods','codes':['002082'],
                               'pairs':[[2022,'FY'],[2023,'FY'],[2024,'FY']],
                               'metrics':['total_operating_revenue']},
                 'outcome':{'status':'answered'},'facts':[]}}
    failures=score(r,SNAPSHOT)['failures']
    assert 'fact_request_answered_as_catalog' in failures
    assert 'requested_year_not_disclosed' in failures
    r['result']['answer']['content']='| 公司 | 年份 | 报告期 | 指标 | 数值 |\n|---|---|---|---|---|\n| 万邦德 | 2024年 | 全年 | 营收 | 当前查询范围内无记录 |'
    r['result']['query_plan']['intent']='facts'
    assert 'fact_request_answered_as_catalog' not in score(r,{'tables':{'income_sheet':[]}})['failures']

def test_incomplete_two_period_comparison_needs_explicit_limitation():
    snapshot={'tables':{'income_sheet':[{'stock_code':'600222','stock_abbr':'太龙药业',
        'report_year':2024,'report_period':'Q3','total_operating_revenue':134067.32}]}}
    r={'case':{'id':'comparison-5','category':'comparison',
               'question':'太龙药业23年前三季度和24年前三季度营收比一比',
               'expected':{'codes':['600222'],'years':[2023,2024],
                           'fields':['total_operating_revenue'],'period':'Q3'}},
       'result':{'answer':{'content':'2023年前三季度无记录；2024年前三季度收入134,067.32万元。'},
                 'outcome':{'status':'partial'},'facts':[]}}
    assert 'incomplete_comparison_not_explained' in score(r,snapshot)['failures']
    r['result']['answer']['content']+=' 缺少2023年同口径值，无法比较增减。'
    assert 'incomplete_comparison_not_explained' not in score(r,snapshot)['failures']

def test_causal_change_question_must_check_comparison_premise():
    r={'case':{'id':'evidence-5','category':'evidence',
               'question':'万邦德24年收入少了，是因为疫情吗？别猜',
               'expected':{'codes':['002082'],'years':[2024],
                           'fields':['total_operating_revenue'],'period':'FY'}},
       'result':{'answer':{'content':'万邦德2024年营收100万元。无法确认疫情是否为原因。'},
                 'query_plan':{'codes':['002082'],'pairs':[[2024,'FY']],
                               'metrics':['total_operating_revenue'],'needs_evidence':True},
                 'outcome':{'status':'partial'},'facts':[]}}
    assert 'change_premise_unchecked' in score(r,SNAPSHOT)['failures']
    r['result']['answer']['content']+=' 单年数字无法判断收入是否下降。'
    assert 'change_premise_unchecked' not in score(r,SNAPSHOT)['failures']

def test_direct_equality_question_needs_plain_conclusion():
    snapshot={'tables':{'core_performance_indicators_sheet':[{'stock_code':'002821',
        'report_year':2023,'report_period':'FY','net_profit_margin':28.99,
        'gross_profit_margin':51.16}]}}
    r={'case':{'id':'ambiguity-4','category':'ambiguity',
               'question':'凯莱英23年净利率和毛利率一样吗，分别多少',
               'expected':{'codes':['002821'],'years':[2023],
                           'fields':['net_profit_margin','gross_profit_margin'],'period':'FY'}},
       'result':{'answer':{'content':'| 指标 | 值 |\n|---|---|\n| 净利率 | 28.99% |\n| 毛利率 | 51.16% |'},
                 'outcome':{'status':'answered'},'facts':[]}}
    assert 'equality_question_not_answered' in score(r,snapshot)['failures']
    r['result']['answer']['content']+='\n这两项不一样。'
    assert 'equality_question_not_answered' not in score(r,snapshot)['failures']

def test_before_after_report_requires_identical_scoring_policy(tmp_path,monkeypatch):
    case_rows=[{'id':f'case-{i}','question':'同一个问题'} for i in range(120)]
    for name,policy in (('before','old'),('after','new')):
        folder=tmp_path/name; folder.mkdir()
        (folder/'cases.json').write_text(json.dumps(case_rows),encoding='utf-8')
        (folder/'records.jsonl').write_text('\n'.join(json.dumps({'case':case,'elapsed':1,
            'result':{}}) for case in case_rows),encoding='utf-8')
        (folder/'score.json').write_text(json.dumps({'completed':120,'automatic_pass':120,
            'review_required':0,'failure_categories':{},'scorer_sha256':policy,
            'results':[{'id':case['id'],'category':'time','automatic_pass':True,
                        'available_value_case':False,'failures':[]} for case in case_rows]}),encoding='utf-8')
        (folder/'snapshot.json').write_text(json.dumps({'tables':{'income_sheet':[]}}),encoding='utf-8')
    monkeypatch.setattr(sys,'argv',['report','--before',str(tmp_path/'before'),
        '--after',str(tmp_path/'after'),'--output',str(tmp_path/'report.md')])
    with pytest.raises(ValueError,match='same scorer version'):
        report_main()

def test_holdout_report_requires_the_same_frozen_database(tmp_path,monkeypatch):
    def write_run(name,count,rows):
        folder=tmp_path/name; folder.mkdir()
        case_rows=[{'id':f'case-{i}','question':'test question'} for i in range(count)]
        (folder/'cases.json').write_text(json.dumps(case_rows),encoding='utf-8')
        (folder/'records.jsonl').write_text('\n'.join(json.dumps({
            'case':case,'elapsed':1,'result':{}}) for case in case_rows),encoding='utf-8')
        (folder/'score.json').write_text(json.dumps({'completed':count,
            'automatic_pass':count,'review_required':0,'failure_categories':{},
            'scorer_sha256':'frozen-policy','results':[{'id':case['id'],
                'category':'time','automatic_pass':True,'available_value_case':False,
                'failures':[]} for case in case_rows]}),encoding='utf-8')
        (folder/'snapshot.json').write_text(json.dumps({'tables':{
            'income_sheet':rows}}),encoding='utf-8')
        return folder

    before=write_run('before',120,[])
    after=write_run('after',120,[])
    holdout=write_run('holdout',30,[{'stock_code':'002082'}])
    (after/'holdout-frozen.json').write_text((holdout/'cases.json').read_text(encoding='utf-8'),encoding='utf-8')
    output=tmp_path/'report.md'
    monkeypatch.setattr(sys,'argv',['report','--before',str(before),'--after',str(after),
        '--holdout',str(holdout),'--output',str(output)])
    with pytest.raises(ValueError,match='Holdout database snapshot differs'):
        report_main()

    (holdout/'snapshot.json').write_text(json.dumps({'tables':{'income_sheet':[]}}),encoding='utf-8')
    report_main()
    assert output.exists()

    (after/'holdout-frozen.json').write_text('[]',encoding='utf-8')
    with pytest.raises(ValueError,match='questions differ'):
        report_main()


def _fact(code,name,year,value,period='FY'):
    return {'stock_code':code,'stock_abbr':name,'report_year':year,'report_period':period,
            'field':'total_operating_revenue','value':value}

def test_explicit_year_comparison_requires_direction_and_inherits_format_followup():
    facts=[_fact('002821','凯莱英',2023,100,'Q3'),
           _fact('002821','凯莱英',2024,80,'Q3')]
    expected={'codes':['002821'],'years':[2023,2024],
              'fields':['total_operating_revenue'],'period':'Q3'}
    r={'case':{'id':'dialog-4-2','category':'dialog','question':'那跟23年比呢'},
       'result':{'answer':{'content':'| 年份 | 营收 |\n|---|---|\n| 2023 | 100万元 |\n| 2024 | 80万元 |'},
                 'facts':facts}}
    assert 'explicit_comparison_lacks_conclusion' in score(r,{'tables':{}})['failures']
    r['result']['answer']['content']+='\n2024年较2023年减少20万元。'
    assert 'explicit_comparison_lacks_conclusion' not in score(r,{'tables':{}})['failures']
    r['case']['id']='dialog-4-3'; r['case']['question']='用亿元'
    r['history']=[{'role':'user','content':'凯莱英24年前三季度营收多少'},
                  {'role':'user','content':'那跟23年比呢'}]
    r['result']['answer']['content']='| 年份 | 营收 |\n|---|---|\n| 2023 | 0.01亿元 |\n| 2024 | 0.008亿元 |'
    assert 'explicit_comparison_lacks_conclusion' in score(r,{'tables':{}})['failures']
    r['case']['question']='把两期收入列成表'
    assert 'explicit_comparison_lacks_conclusion' not in score(r,{'tables':{}})['failures']


def test_precomputed_percentage_rate_cannot_be_compounded_as_yoy():
    facts=[{'fact_id':'r23','stock_code':'002082','report_year':2023,
            'report_period':'FY','field':'net_profit_yoy_growth','value':-47.23,'unit':'%'},
           {'fact_id':'r24','stock_code':'002082','report_year':2024,
            'report_period':'FY','field':'net_profit_yoy_growth','value':12.66,'unit':'%'}]
    record={'case':{'id':'j16','category':'holdout_fresh','question':'万邦德24年净利润同比率多少'},
            'result':{'answer':{'content':'万邦德2024年净利润同比率12.66%。'},
                      'facts':facts,'derived_facts':[{'source_fact_ids':['r23','r24'],
                        'calculation':'yoy','value':126.805,'field':'net_profit_yoy_growth'}],
                      'outcome':{'status':'answered'}}}
    assert 'percentage_rate_compounded' in score(record,{'tables':{}})['failures']
    record['result']['derived_facts']=[]
    assert 'percentage_rate_compounded' not in score(record,{'tables':{}})['failures']

def test_company_comparison_needs_correct_winner_not_only_two_rows():
    facts=[_fact('002082','万邦德',2024,100),_fact('002864','盘龙药业',2024,80)]
    r={'case':{'id':'dialog-9-1','category':'dialog','question':'万邦德和盘龙药业24年营收对比'},
       'result':{'answer':{'content':'| 公司 | 营收 |\n|---|---|\n| 万邦德 | 100万元 |\n| 盘龙药业 | 80万元 |'},
                 'facts':facts}}
    assert 'explicit_comparison_lacks_conclusion' in score(r,{'tables':{}})['failures']
    r['result']['answer']['content']+='\n盘龙药业更高。'
    assert 'explicit_comparison_lacks_conclusion' in score(r,{'tables':{}})['failures']
    r['result']['answer']['content']+='\n万邦德比盘龙药业高。'
    assert 'explicit_comparison_lacks_conclusion' not in score(r,{'tables':{}})['failures']

def test_two_company_two_year_comparison_needs_each_year_or_shared_winner():
    facts=[_fact('002821','凯莱英',2022,100),_fact('002821','凯莱英',2023,80),
           _fact('600085','同仁堂',2022,150),_fact('600085','同仁堂',2023,180)]
    r={'case':{'id':'comparison-6','category':'comparison',
               'question':'凯莱英跟同仁堂库里最新三年年报营收对比',
               'expected':{'codes':['002821','600085'],'years':[2022,2023],
                           'fields':['total_operating_revenue'],'period':'FY'}},
       'result':{'answer':{'content':'2022年和2023年数据已列成表。'},'facts':facts}}
    assert 'explicit_comparison_lacks_conclusion' in score(r,{'tables':{}})['failures']
    r['result']['answer']['content']='2022年同仁堂高于凯莱英；2023年同仁堂高于凯莱英。'
    assert 'explicit_comparison_lacks_conclusion' not in score(r,{'tables':{}})['failures']
    r['result']['answer']['content']='这两年同仁堂均比凯莱英高。'
    assert 'explicit_comparison_lacks_conclusion' not in score(r,{'tables':{}})['failures']
