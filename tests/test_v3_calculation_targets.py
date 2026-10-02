"""作品说明：比较基期不是额外的同比目标；多年度各年同比仍逐年执行。"""
from src.agent.v3.executor import calculation_targets
from src.agent.v3.contracts import Request,Conditions,Goal

def request(question):
    return Request(question=question,turn_id='test',goals=[Goal(id='g',kind='lookup',text=question)],conditions=Conditions(codes=['600080'],metrics=['attributable_net_profit'],calculation='yoy',comparison_axis='years',time={'mode':'explicit','years':[2022,2023],'periods':['FY']}))

def test_base_year_does_not_require_previous_growth():
    selection={'600080':[(2022,'FY'),(2023,'FY')]}
    r=request('计算2023年净利润相对2022年的同比增长率')
    assert calculation_targets(r,selection)=={'600080':[(2023,'FY')]}

def test_each_year_yoy_still_requires_each_base():
    selection={'600080':[(2022,'FY'),(2023,'FY')]}
    assert calculation_targets(request('分别计算2022和2023年净利润同比'),selection)==selection
