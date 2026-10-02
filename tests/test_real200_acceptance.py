"""作品说明：独立评分必须识别口径、金额、单位与图表错误，不能因生产核验自报通过而放行。"""
import copy
from eval.real200_acceptance import score,summarize

def sample():
    fact={'id':'a','stock_code':'600085','company':'同仁堂','year':2024,'period':'FY','metric':'operating_revenue','scope':'consolidated','unit':'元','data_version':'v','value':'123456789','value_exact':'123456789'}
    condition={'codes':['600085'],'metrics':['operating_revenue'],'scope':'consolidated','time':{'years':[2024],'periods':['FY']},'presentation':{'unit':'亿元','decimals':2},'restrictions':{}}
    case={'id':'x','group':None,'category':'查询口径','expect':{'kind':'financial','codes':['600085'],'metrics':['operating_revenue'],'years':[2024],'periods':['FY'],'scope':'consolidated','goals':['lookup'],'unit':'亿元','decimals':2}}
    result={'data_version':'v','answer':{'content':'同仁堂为1.23亿元'},'facts':[fact],'request_contract':{'conditions':condition,'goals':[{'id':'g','kind':'lookup'}]},'diagnostics':{'timings':[{'model':'q'}]},'outcome':{'status':'answered'},'task_results':[{'kind':'lookup','status':'completed'}]}
    return case,{'task':{'status':'completed','saved':True,'result':result}}, {'version':'v','facts':{'a':fact},'reports':[{'stock_code':'600085','year':2024,'period':'FY'}]}, {'cards':{'a':{'value_exact':'123456789'}}}

def test_independent_baseline():
    c,r,f,o=sample();assert score(c,r,f,o,'q')['passed']

def test_runtime_self_pass_cannot_cover_wrong_number():
    c,r,f,o=sample();r=copy.deepcopy(r);r['task']['result']['facts'][0]['value_exact']='12';r['task']['result']['verification_v3']={'status':'pass'}
    v=score(c,r,f,o,'q');assert not v['passed'] and 'wrong_numeric_fact' in v['critical']

def test_display_and_scope_are_checked():
    c,r,f,o=sample();r=copy.deepcopy(r);r['task']['result']['answer']['content']='123456789万元';r['task']['result']['facts'][0]['scope']='parent'
    v=score(c,r,f,o,'q');assert 'fact_identity_changed' in v['critical'] and 'requested_number_display_missing' in v['errors']

def test_dialogue_groups_and_threshold_do_not_hide_failures():
    rows=[]
    for i in range(200):rows.append({'case':{'id':str(i),'category':'x','group':'g' if i<4 else None},'score':{'passed':i<170,'automated_pass':i<170,'errors':[],'critical':[],'review_status':'not_required'}})
    assert summarize(rows)['acceptance_passed']
    rows[0]['score']['critical']=['wrong_source'];assert not summarize(rows)['acceptance_passed']
    rows[0]['score']['critical']=[];rows[0]['score']['passed']=False;assert not summarize(rows)['dialogue_groups']['g']
