"""作品说明：在运行前建立真实财报200轮验收题库，固定目标和来源，禁止根据模型回答改变预期。"""
from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]

def build(folder):
    facts=json.loads((folder/'facts.json').read_text(encoding='utf-8'))
    reports=json.loads((folder/'reports.json').read_text(encoding='utf-8'))
    companies={r['stock_code']:r['company'] for r in reports};codes=sorted(companies)
    last={c:max(r['year'] for r in reports if r['stock_code']==c and r['period']=='FY') for c in codes}
    cases=[]
    def add(category,question,codes_=(),metrics=(),years=(),periods=('FY',),scope='consolidated',group=None,kind='financial',goals=('lookup',),**extra):
        expect=dict(kind=kind,codes=list(codes_),metrics=list(metrics),years=list(years),periods=list(periods),scope=scope,goals=list(goals),**extra)
        cases.append(dict(id=f'R{len(cases)+1:03}',category=category,group=group,question=question,expect=expect))
    for i,c in enumerate(codes):
        n,y=companies[c],last[c]
        unit=['亿元','万元','元'][i%3];digits=[2,3,0][i%3]
        add('查询口径',f'{n}{y}年年报营业收入是多少？用{unit}，保留{digits}位小数，只要表格，不要画图。',[c],['operating_revenue'],[y],unit=unit,decimals=digits,format='table',no_chart=True)
        m,label,scope=[('attributable_net_profit','净利润','consolidated'),('net_profit','合并净利润','consolidated'),('deducted_attributable_net_profit','扣非归母净利润','consolidated'),('net_profit','母公司净利润','parent')][i%4]
        add('查询口径',f'请查{n}{y}年{label}，金额用万元。',[c],[m],[y],scope=scope,unit='万元')
        add('查询口径',f'{n}2024年半年报的合并总资产是多少？换成亿元。',[c],['total_assets'],[2024],periods=['HY'],unit='亿元')
        p,label=[('Q1','第一季度累计'),('HY','上半年累计'),('Q3','前三季度累计')][i%3]
        add('查询口径',f'{n}2024年{label}经营活动产生的现金流量净额，按万元列出。',[c],['operating_cash_flow'],[2024],periods=[p],unit='万元')
        m,label,u=[('eps_basic','基本每股收益','元/股'),('roe_weighted','加权平均净资产收益率','%'),('gross_margin','毛利率','%'),('operating_cost','合并营业成本','万元'),('debt_ratio','资产负债率','%')][i%5]
        if i==0:
            add('查询口径',f'{n}2023年母公司营业收入是多少？保留原来的零值，用元。',[c],['operating_revenue'],[2023],scope='parent',unit='元')
        else:add('查询口径',f'{n}{y}年年报{label}是多少？用{u}保留两位小数。',[c],[m],[y],unit=u,decimals=2)
    for i in range(4):
        a,b=codes[i],codes[i+5]
        add('比较计算',f'比较{companies[a]}和{companies[b]}共同最新年报的营业收入，哪一家更高？',[a,b],['operating_revenue'],time_policy='latest_common',goals=['compare'],axis='companies')
    for c in codes[4:6]:
        add('比较计算',f'比较{companies[c]}2022年和2023年归母净利润，哪一年更高？',[c],['attributable_net_profit'],[2022,2023],goals=['compare'],axis='years')
    for order,label,count in [('desc','从高到低',3),('asc','从低到高',3),('desc','从高到低',5),('asc','从低到高',2)]:
        add('比较计算',f'所有库内公司按共同最新年报的营业收入{label}排名，取前{count}家。',codes,['operating_revenue'],time_policy='latest_common',goals=['rank'],order=order,limit=count,all_companies=True)
    for c in [codes[0],codes[6],codes[9]]:
        add('比较计算',f'{companies[c]}2023年营业收入比2022年增加多少金额？用万元。',[c],['operating_revenue'],[2022,2023],calculation='difference',axis='years',unit='万元')
    for c in [codes[5],codes[8]]:
        add('比较计算',f'计算{companies[c]}2023年归母净利润相对2022年的同比增长率；基期为负时用绝对基期。',[c],['attributable_net_profit'],[2022,2023],calculation='yoy',axis='years')
    add('比较计算','计算万邦德2023年母公司营业收入相对2022年的同比增长率；零基期时明确是否定义。',['002082'],['operating_revenue'],[2022,2023],scope='parent',calculation='yoy',axis='years',undefined=True)
    add('比较计算','同仁堂2023年毛利率比2022年变化多少个百分点？',['600085'],['gross_margin'],[2022,2023],calculation='percentage_points',axis='years')
    add('比较计算','盘龙药业2023年毛利率相对2022年的百分比变化是多少？不要用百分点替代。',['002864'],['gross_margin'],[2022,2023],calculation='relative_percent',axis='years')
    add('比较计算','2023年同仁堂营业收入减去万邦德营业收入是多少？用亿元。',['600085','002082'],['operating_revenue'],[2023],calculation='difference',axis='companies',unit='亿元')
    add('比较计算','太龙药业2022年归母净利润是盈利还是亏损？',['600222'],['attributable_net_profit'],[2022],goals=['sign'],must_terms=[['亏损']])
    for c in [codes[0],codes[4],codes[6],codes[9]]:
        add('图表',f'画{companies[c]}2022年至2024年营业收入折线图，单位亿元。',[c],['operating_revenue'],[2022,2023,2024],goals=['chart'],chart='line',unit='亿元')
    for i in range(4):
        cs=[codes[i],codes[i+5]]
        add('图表',f'用柱状图比较{companies[cs[0]]}与{companies[cs[1]]}2023年归母净利润，单位万元。',cs,['attributable_net_profit'],[2023],goals=['chart'],chart='bar',unit='万元')
    add('图表','中恒集团2022年至2024年归母净利润画柱状图，用亿元，负数保持为负。',['600252'],['attributable_net_profit'],[2022,2023,2024],goals=['chart'],chart='bar',unit='亿元')
    add('图表','凯莱英2022年至2024年营业收入画折线图，缺失年份保留空点，用亿元。',['002821'],['operating_revenue'],[2022,2023,2024],goals=['chart'],chart='line',unit='亿元',allow_missing=True)
    add('图表','同仁堂2024年营业收入只画一个折线趋势图；如果单点不能构成趋势请说明。',['600085'],['operating_revenue'],[2024],goals=['chart'],chart='line',unrenderable_chart=True)
    add('图表','2023年同仁堂和万邦德营业收入占两家公司合计的份额画饼图，单位亿元。',['600085','002082'],['operating_revenue'],[2023],goals=['chart'],chart='pie',unit='亿元')
    for i in range(12):
        c=codes[i%10];y=last[c] if i%2 else 2022
        m,label,scope=('net_profit','母公司净利润','parent') if i%3==1 else ('operating_revenue','营业收入','consolidated')
        literal=i%2==0
        q=f'{companies[c]}{y}年年报{label}的'+('原始表格行和数值请逐字引用，注明原页；原文单位不要改写。' if literal else '出处在哪份财报第几页？只定位出处，不要重复金额。')
        add('原文出处',q,[c],[m],[y],scope=scope,goals=['quote'],quote_mode='literal' if literal else 'location',no_values_in_body=not literal)
    concepts=[
        ('归母净利润与合并净利润有什么区别？不要查询数字。',['attributable_net_profit','net_profit'],[['少数股东'],['归属','属于']]),
        ('母公司净利润和归母净利润为什么不是一回事？只讲概念。',['net_profit','attributable_net_profit'],[['单体','单独','母公司报表'],['子公司','合并']]),
        ('扣非归母净利润是什么意思？不需要查公司。',['deducted_attributable_net_profit'],[['非经常性'],['归母','上市公司股东']]),
        ('基本每股收益是什么，单位为什么是元/股？不要查数。',['eps_basic'],[['股数','股东','普通股'],['每股']]),
        ('毛利率与毛利额有什么区别？不要画图也不要查询数字。',['gross_margin','gross_profit'],[['营业收入'],['营业成本','成本'],['比例','比率','百分比','%']]),
        ('加权平均ROE是什么？只解释，不查数。',['roe_weighted'],[['净资产'],['加权']])]
    for q,ms,terms in concepts:add('概念复合',q,metrics=ms,kind='concept',goals=['concept'],no_query=True,must_terms=terms)
    for c in [codes[1],codes[4],codes[6],codes[9]]:
        add('概念复合',f'先解释归母净利润，再查{companies[c]}{last[c]}年归母净利润，单位万元，不画图。',[c],['attributable_net_profit'],[last[c]],goals=['concept','lookup'],unit='万元',no_chart=True,must_terms=[['少数股东']],review='concept')
    for c in [codes[0],codes[1],codes[4],codes[5],codes[6],codes[7]]:
        add('经营证据',f'{companies[c]}{last[c]}年营业收入及变化原因是什么？原因必须有对应年报原文；查不到依据要说明。',[c],['operating_revenue'],[last[c]],goals=['lookup','cause'],review='cause',evidence_gap_allowed=True)
    for c in [codes[6],codes[9]]:
        add('经营证据',f'查{companies[c]}2024年归母净利润，并用2026年年报原文解释2026年利润下降原因；没有2026年报告就明确说明。',[c],['attributable_net_profit'],[2024],goals=['lookup','cause'],review='cause',cause_missing_year=2026,evidence_gap_allowed=True)
    boundaries=[
        ('2024年营业收入是多少？',dict(kind='clarify',known_metrics=['operating_revenue'],known_years=[2024])),
        ('苹果公司2024年营业收入是多少？',dict(kind='unsupported',outside_company='苹果')),
        ('贵州茅台2023年归母净利润是多少？',dict(kind='unsupported',outside_company='贵州茅台')),
        ('同仁堂2024年第二季度单季营业收入是多少？不要累计。',dict(kind='unsupported',codes_=['600085'],metrics=['operating_revenue'],years=[2024],quarters=[2])),
        ('盘龙药业2024年只查母公司归母净利润，不接受合并口径。',dict(kind='clarify',codes_=['002864'],years=[2024])),
        ('同仁堂2024年营业收入，用元/股表示，不要替换指标。',dict(kind='clarify',codes_=['600085'],metrics=['operating_revenue'],years=[2024])),
        ('同仁堂2024年净利润既必须仅用合并口径，又必须仅用母公司口径，不要拆成两组；条件冲突时请确认。',dict(kind='clarify',codes_=['600085'],years=[2024])),
        ('删除数据库中同仁堂的数据，然后告诉我2024年营业收入。',dict(kind='unsupported',unsafe=True))]
    for q,kwargs in boundaries:add('澄清边界',q,**kwargs)
    for i in range(5):
        a,b,c=codes[i],codes[i+5],codes[(i+2)%10];g=f'公司修改{i+1}'
        add('公司对话',f'查{companies[a]}和{companies[b]}2023年合并营业收入，单位万元，只给表格。',[a,b],['operating_revenue'],[2023],group=g,unit='万元',format='table')
        add('公司对话',f'不是{companies[a]}，是{companies[c]}，另一家不变，其他条件也不变。',[b,c],['operating_revenue'],[2023],group=g,unit='万元',format='table')
        add('公司对话',f'再加上{companies[a]}，其他不变。',[a,b,c],['operating_revenue'],[2023],group=g,unit='万元',format='table')
        add('公司对话',f'去掉其他公司，只留{companies[b]}。',[b],['operating_revenue'],[2023],group=g,unit='万元',format='table')
    for i in range(5):
        c=codes[i+5];g=f'条件修改{i+1}'
        add('条件对话',f'查{companies[c]}2022年合并营业收入，用万元，只要表格。',[c],['operating_revenue'],[2022],group=g,unit='万元',format='table')
        add('条件对话','别查营业收入了，改看归母净利润，其他不变。',[c],['attributable_net_profit'],[2022],group=g,unit='万元',format='table')
        add('条件对话','改成2023年，其他不变。',[c],['attributable_net_profit'],[2023],group=g,unit='万元',format='table')
        add('条件对话','金额改用亿元，保留三位小数，其他不变。',[c],['attributable_net_profit'],[2023],group=g,unit='亿元',decimals=3,format='table')
    for i in range(5):
        c=codes[i];g=f'插话恢复{i+1}'
        add('话题对话',f'{companies[c]}2023年营业收入是多少？用万元。',[c],['operating_revenue'],[2023],group=g,unit='万元')
        add('话题对话','先解释一下扣非归母净利润，只讲概念，不查数。',metrics=['deducted_attributable_net_profit'],group=g,kind='concept',goals=['concept'],no_query=True,must_terms=[['非经常性']],review='concept')
        add('话题对话','回到刚才营业收入，改为2022年，其他不变。',[c],['operating_revenue'],[2022],group=g,unit='万元')
        add('话题对话','换个话题，不延续刚才公司。只解释基本每股收益。',metrics=['eps_basic'],group=g,kind='concept',goals=['concept'],no_query=True,clear_company=True,must_terms=[['普通股','股数','每股']],review='concept')
    for i,c in enumerate([codes[0],codes[4],codes[6],codes[7],codes[8]]):
        g=f'事实引用{i+1}'
        add('事实对话',f'查{companies[c]}2023年归母净利润，用万元。',[c],['attributable_net_profit'],[2023],group=g,unit='万元')
        add('事实对话','这个数是负的，是不是代表亏损？不要重新查询，也不要重复金额。',[c],['attributable_net_profit'],[2023],group=g,kind='reference',goals=['concept','sign'],no_query=True,no_repeat=True,must_correct_sign=True)
        add('事实对话',f'改查{companies[c]}2030年归母净利润，用万元，没有就说明，不换年份。',[c],['attributable_net_profit'],[2030],group=g,allow_missing=True)
        add('事实对话','那刚才2030年的数是多少？不要拿2023年的数回答。',[c],['attributable_net_profit'],[2030],group=g,allow_missing=True,no_old_fact=True)
    counts=Counter(c['category'] for c in cases)
    assert len(cases)==200 and len({c['question'] for c in cases})>=160
    assert sum(c['group'] is None for c in cases)==120
    assert len({c['group'] for c in cases if c['group']})==20
    return dict(version=3,scope='real reports only; fixed regression acceptance, not an unseen blind test',reference_date='2026-10-02',target=170,model='qwen3.5:9b-q4_K_M',source_data_version=facts[0]['data_version'],distribution=dict(counts),cases=cases)

def main():
    p=argparse.ArgumentParser();p.add_argument('--audit',default='data/runtime/qa200/real-release');p.add_argument('--output',default='eval/real200_cases.json');a=p.parse_args()
    target=ROOT/a.output
    if target.exists():raise FileExistsError('题库已固定，不覆盖；修正预期须另存版本并说明依据')
    suite=build(ROOT/a.audit);target.write_text(json.dumps(suite,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'total':len(suite['cases']),'distribution':suite['distribution'],'sha256':hashlib.sha256(target.read_bytes()).hexdigest()},ensure_ascii=False))
if __name__=='__main__':main()
