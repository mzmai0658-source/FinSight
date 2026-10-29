"""固定常见问法家族、完整对话组及真实原件标准，不使用生产语义解析器生成预期。"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {
    '查数指标口径报告期': (20, 4), '比较排名计算': (10, 2),
    '单位精度表格图表': (8, 2), '概念出处经营原因': (8, 1),
    '缺条件库外资料缺失': (6, 1), '常见否定复合要求': (8, 2),
}
DIALOGUES = {
    '公司指标年份修改': (8, 1), '概念插话恢复': (6, 1), '事实指代原页': (6, 1),
    '澄清后继续': (4, 1), '复合目标部分证据缺失': (4, 1), '话题清空': (2, 1),
}
LABELS = {
    'operating_revenue':'营业收入','total_operating_revenue':'营业总收入',
    'main_business_revenue':'主营业务收入','net_profit':'净利润合计',
    'attributable_net_profit':'归母净利润','deducted_attributable_net_profit':'扣非归母净利润',
    'gross_profit':'毛利额','gross_margin':'毛利率','eps_basic':'基本每股收益',
    'roe_weighted':'加权平均净资产收益率','total_assets':'总资产','total_liabilities':'总负债',
    'total_equity':'股东权益合计','cash':'货币资金','inventory':'存货','receivables':'应收账款',
    'operating_cash_flow':'经营活动产生的现金流量净额',
}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def goal(kind='lookup',codes=(),metrics=(),years=(),scope='consolidated',periods=('FY',),**extra):
    return dict(kind=kind,codes=list(codes),metrics=list(metrics),years=list(years),
                periods=list(periods),scope=scope,**extra)


def expected(*goals,mode='financial',**extra):
    return dict(kind=mode,goal_expectations=list(goals),**extra)


def build_cases(reference,seed=20261003):
    facts=reference['facts']; companies={f['stock_code']:f['company'] for f in facts.values()}
    codes=sorted(companies); assert len(codes)==10
    identities={(f['stock_code'],f['year'],f['period'],f['metric'],f['scope']) for f in facts.values()}
    reports={(r['stock_code'],r['year'],r['period']) for r in reference['reports']}
    families=[]; groups=[]

    def choose(index,metric='operating_revenue',years=(2024,),scope='consolidated',period='FY'):
        eligible=[c for c in codes if all((c,y,period,metric,scope) in identities for y in years)]
        assert eligible,(metric,years,scope,period)
        return eligible[index%len(eligible)]

    def missing(c):
        y=next(y for y in (2021,2025) if (c,y,'FY') not in reports)
        assert not any(f['stock_code']==c and f['year']==y and f['period']=='FY' for f in facts.values())
        return y

    def family(category,questions,e):
        assert len(questions)==3 and len(set(questions))==3
        families.append(dict(id='F'+str(len(families)+1).zfill(2),category=category,questions=questions,expect=e))

    def group(category,turns):
        assert len(turns)==4
        groups.append(dict(id='D'+str(len(groups)+1).zfill(2),category=category,turns=turns))

    cat='查数指标口径报告期'
    basics=[('operating_revenue','consolidated','FY'),('total_operating_revenue','consolidated','FY'),
        ('main_business_revenue','consolidated','FY'),('net_profit','consolidated','FY'),
        ('attributable_net_profit','consolidated','FY'),('deducted_attributable_net_profit','consolidated','FY'),
        ('net_profit','parent','FY'),('operating_revenue','parent','FY'),('gross_profit','consolidated','FY'),
        ('gross_margin','consolidated','FY'),('eps_basic','consolidated','FY'),('roe_weighted','consolidated','FY'),
        ('total_assets','consolidated','FY'),('total_liabilities','consolidated','FY'),('total_equity','consolidated','FY'),
        ('cash','consolidated','FY'),('inventory','consolidated','FY'),('receivables','consolidated','FY'),
        ('operating_revenue','consolidated','HY'),('operating_cash_flow','consolidated','Q3')]
    for i,(metric,scope,period) in enumerate(basics):
        c=choose(i,metric,scope=scope,period=period); n=companies[c]
        t={'FY':'2024年年报','HY':'2024年半年报累计','Q3':'2024年前三季度累计'}[period]
        label=('母公司单体' if scope=='parent' else '合并报表')+LABELS[metric]
        unit='元/股' if metric=='eps_basic' else '%' if metric in {'gross_margin','roe_weighted'} else '万元'
        family(cat,[f'请查询{n}{t}的{label}，单位{unit}。',
                    f'{n}{t}，{label}是多少？用{unit}回答。',
                    f'我想看{n}{t}的{label}，请按{unit}显示。'],
               expected(goal(codes=[c],metrics=[metric],years=[2024],scope=scope,periods=[period],unit=unit)))

    cat='比较排名计算'
    for i,metric in enumerate(['operating_revenue','attributable_net_profit','total_assets']):
        a=choose(i,metric); b=choose(i+4,metric); assert a!=b
        names=f'{companies[a]}和{companies[b]}'; label=LABELS[metric]
        family(cat,[f'比较{names}2024年的合并{label}，哪家更高？单位万元。',
                    f'2024年合并{label}，{names}谁更多？请用万元比较。',
                    f'请对比{names}2024年合并{label}，用万元给出高低结论。'],
               expected(goal('compare',[a,b],[metric],[2024],unit='万元',axis='companies')))
    computations=[('operating_revenue','difference'),('attributable_net_profit','difference'),
                  ('operating_revenue','yoy'),('gross_margin','relative_percent'),('gross_margin','percentage_points')]
    for i,(metric,method) in enumerate(computations):
        c=choose(i+3,metric,years=(2023,2024)); n=companies[c]; label=LABELS[metric]
        phrases={'difference':('增加或减少了多少金额','计算两年的差额','用2024年减2023年'),
                 'yoy':('根据基础数字计算2024年同比增长率','计算2024年比2023年的同比变化率','以2023年为基期算同比增长率'),
                 'relative_percent':('计算相对百分比变化','相对2023年变化了百分之多少','算相对变化率，不要百分点差'),
                 'percentage_points':('增加或减少了多少个百分点','计算百分点变化','两年相差多少个百分点，不算相对变化率')}[method]
        q=[f'{n}2023年和2024年合并{label}，{p}。'+('单位万元。' if method=='difference' else '') for p in phrases]
        kwargs=dict(calculation=method,axis='years')
        if method=='difference':kwargs['unit']='万元'
        family(cat,q,expected(goal('compare',[c],[metric],[2023,2024],**kwargs)))
    for i,(metric,order) in enumerate([('operating_revenue','desc'),('attributable_net_profit','asc')]):
        eligible=[c for c in codes if (c,2023,'FY',metric,'consolidated') in identities]
        direction='最高' if order=='desc' else '最低'; sequence='降序' if order=='desc' else '升序'
        family(cat,[f'库内2023年合并{LABELS[metric]}{direction}的前三家公司是谁？单位万元。',
                    f'按{sequence}列出全库2023年{LABELS[metric]}前三家公司，用万元。',
                    f'对库内公司按2023年合并{LABELS[metric]}排序，取{direction}三家，万元。'],
               expected(goal('rank',eligible,[metric],[2023],order=order,limit=3,unit='万元',all_companies=True)))

    cat='单位精度表格图表'
    for i,(unit,digits) in enumerate([('元',0),('万元',3),('亿元',4)]):
        c=choose(i+2); n=companies[c]
        family(cat,[f'查询{n}2024年营业收入，用{unit}，保留{digits}位小数。',
                    f'{n}2024年营业收入，{unit}、{digits}位小数。',
                    f'请按{unit}显示{n}2024年营业收入，小数点后{digits}位。'],
               expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit=unit,decimals=digits)))
    c=choose(5); n=companies[c]
    family(cat,[f'{n}2024年营业收入，用万元两位小数，只给表格。',
                f'请把{n}2024年营业收入做成表格，万元，两位小数。',
                f'{n}2024年营业收入，万元两位小数，表格回答。'],
           expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit='万元',decimals=2,format='table'),no_chart=True))
    pair=[choose(6),choose(8)]; names='和'.join(companies[c] for c in pair)
    family(cat,[f'列{names}2024年营业收入，亿元三位小数，表格即可。',
                f'{names}2024年营业收入，请用亿元保留三位小数做成表格。',
                f'请给{names}2024年营业收入表格，单位亿元，小数点后三位。'],
           expected(goal(codes=pair,metrics=['operating_revenue'],years=[2024],unit='亿元',decimals=3,format='table'),no_chart=True))
    for i,(metric,chart,unit) in enumerate([('operating_revenue','bar','亿元'),('operating_revenue','line','万元'),('gross_margin','line','%')]):
        c=choose(i+4,metric,years=(2022,2023,2024)); n=companies[c]; name='柱状图' if chart=='bar' else '折线图'
        family(cat,[f'画{n}2022年至2024年{LABELS[metric]}{name}，单位{unit}，两位小数。',
                    f'{n}2022、2023、2024年{LABELS[metric]}，请用{name}展示，纵轴{unit}，保留两位。',
                    f'把{n}2022到2024年的{LABELS[metric]}做成{name}，用{unit}显示两位小数。'],
               expected(goal('chart',[c],[metric],[2022,2023,2024],chart=chart,unit=unit,decimals=2)))

    cat='概念出处经营原因'
    concepts=[(['attributable_net_profit'],'归母净利润是什么意思','definition',[['归母'],['股东','所有者']]),
        (['eps_basic'],'基本每股收益是什么意思','definition',[['每股','每一股'],['净利润']]),
        (['gross_margin'],'毛利率是什么','definition',[['收入'],['成本']]),
        (['attributable_net_profit','deducted_attributable_net_profit'],'归母净利润和扣非归母净利润有什么区别','difference',[['非经常性'],['归母']])]
    for metrics,text,mode,terms in concepts:
        family(cat,[text+'？不要查数字。','请解释'+text+'，只讲概念。','我想了解'+text+'，不查询公司数据。'],
               expected(goal('concept',metrics=metrics,concept_mode=mode),mode='concept',no_query=True,must_terms=terms))
    for i,(metric,scope) in enumerate([('operating_revenue','consolidated'),('net_profit','parent')]):
        c=choose(i+3,metric,scope=scope); n=companies[c]; label=('母公司单体' if scope=='parent' else '合并')+LABELS[metric]
        family(cat,[f'摘录{n}2024年{label}对应的原表行，保留原表单位并给PDF物理页码。',
                    f'{n}2024年{label}的原文表格行是什么？请逐字引用并注明PDF页码。',
                    f'请找{n}2024年{label}的原始单元格和行名，给原件物理页，不改写金额单位。'],
               expected(goal('quote',[c],[metric],[2024],scope=scope,quote_mode='literal')))
    causes=[('600080',2024,'operating_cash_flow',13,['支付原材料采购款','研发投入']),
            ('600085',2022,'operating_revenue',11,['营销改革','调整部分产品售价'])]
    for c,y,m,page,terms in causes:
        n=companies[c]; label=LABELS[m]
        narrative=dict(stock_code=c,year=y,metric=m,physical_page=page,terms=terms)
        family(cat,[f'{n}{y}年{label}是多少？请根据当年年报解释变化原因，并给物理页码。',
                    f'查询{n}{y}年{label}，再用同年年报说明为什么变化，给原页。',
                    f'请给{n}{y}年{label}数字和年报披露的变动原因，注明PDF页码。'],
               expected(goal(codes=[c],metrics=[m],years=[y],unit='万元'),
                        goal('cause',[c],[m],[y],narrative=narrative,unit='万元')))

    cat='缺条件库外资料缺失'
    family(cat,['请查询2024年营业收入，我还没指定公司，先问我。','2024年营业收入是多少？请先确认我想查哪家公司。','我想查2024年营业收入，暂未选公司。'],
           expected(goal(metrics=['operating_revenue'],years=[2024]),mode='clarify',known_metrics=['operating_revenue'],known_years=[2024]))
    c=choose(4); n=companies[c]
    family(cat,[f'我想查询{n}2024年财务数据，请先问我要看什么指标。',f'{n}2024年的财务数字，我还没指定指标。',f'请看{n}2024年报，我该先说明哪个财务指标？'],
           expected(goal(codes=[c],years=[2024]),mode='clarify',known_codes=[c],known_years=[2024]))
    family(cat,['贵州茅台2024年营业收入是多少？未收录请直接说明。','请查贵州茅台2024年营业收入，库里没有就说明未覆盖。','我想看贵州茅台2024年营业收入，没有资料不要换公司。'],
           expected(goal(metrics=['operating_revenue'],years=[2024]),mode='unsupported',outside_company='贵州茅台'))
    y=missing(c)
    family(cat,[f'查{n}{y}年年报营业收入，没有这份报告就说明。',f'{n}{y}年全年营业收入多少？原件缺失请说明。',f'请查询{n}{y}年度营业收入，没有报告不要用别的年份补。'],
           expected(goal(codes=[c],metrics=['operating_revenue'],years=[y],completion='missing_report'),mode='missing',no_old_fact=True))
    family(cat,[f'请查{n}2024年第二季度单季营业收入。',f'{n}2024年4至6月的营业收入是多少？要第二季度单季。',f'我想看{n}2024年第二季度营业收入，只要单季。'],
           expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],single_quarter=True,quarters=[2]),mode='unsupported',must_terms=[['单季','单季度'],['不支持','暂不','无法']]))
    family(cat,[f'查询{n}2024年母公司单体归母净利润。',f'{n}2024年归母净利润，请只用母公司报表。',f'只看母公司单体口径，{n}2024年归母净利润多少？'],
           expected(goal(codes=[c],metrics=['attributable_net_profit'],years=[2024],scope='parent'),mode='clarify',known_codes=[c],known_metrics=['attributable_net_profit'],known_years=[2024]))

    cat='常见否定复合要求'; c=choose(7); n=companies[c]; a=choose(8); b=choose(2)
    family(cat,[f'{n}2024年只查归母净利润，不查营业收入和营业成本，用万元。',f'查询{n}2024年归母净利润；营业收入、营业成本都不要查，单位万元。',f'我只要{n}2024年归母净利润，别列收入和成本，万元。'],
           expected(goal(codes=[c],metrics=['attributable_net_profit'],years=[2024],unit='万元'),excluded_metrics=['operating_revenue','operating_cost']))
    family(cat,[f'{n}2024年营业收入，只给表格，不画图，用万元。',f'请用表格列{n}2024年营业收入，万元，不要生成图形。',f'{n}2024年营业收入用万元做表格，禁止画图。'],
           expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit='万元',format='table'),no_chart=True))
    family(cat,['不要查询公司数字，解释归母净利润和合并净利润的区别。','只讲合并净利润与归母净利润的区别，不查数字。','归母净利润为什么不等于合并净利润？仅解释概念。'],
           expected(goal('concept',metrics=['net_profit','attributable_net_profit'],concept_mode='difference'),mode='concept',no_query=True,must_terms=[['少数股东'],['归母']]))
    family(cat,[f'先解释毛利率，再查{n}2024年营业收入，用万元。',f'请说明毛利率含义，并给{n}2024年营业收入，单位万元。',f'{n}2024年营业收入多少？万元；另外解释毛利率概念。'],
           expected(goal('concept',metrics=['gross_margin'],concept_mode='definition'),goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit='万元')))
    family(cat,[f'分别查询{companies[a]}2024年营业收入和{companies[b]}2024年归母净利润，用万元。',f'请给{companies[a]}2024年营收，再给{companies[b]}同年归母净利润，单位万元。',f'{companies[a]}2024年营业收入、{companies[b]}2024年归母净利润各是多少？万元。'],
           expected(goal(codes=[a],metrics=['operating_revenue'],years=[2024],unit='万元'),goal(codes=[b],metrics=['attributable_net_profit'],years=[2024],unit='万元')))
    family(cat,[f'分别查{n}2024年母公司净利润和合并归母净利润，用万元。',f'{n}2024年母公司单体净利润是多少？同时查合并归母净利润，万元。',f'请给{n}2024年两项数字：母公司单体净利润、合并归母净利润，用万元。'],
           expected(goal(codes=[c],metrics=['net_profit'],years=[2024],scope='parent',unit='万元'),goal(codes=[c],metrics=['attributable_net_profit'],years=[2024],unit='万元')))
    family(cat,[f'查{n}2024年营业收入，万元，再摘录对应原表行和PDF页码。',f'{n}2024年营业收入多少？用万元回答，并逐字给原表行与物理页。',f'请给{n}2024年营业收入的万元数值，以及原始行和PDF页码。'],
           expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit='万元'),goal('quote',[c],['operating_revenue'],[2024],quote_mode='literal')))
    y=missing(c)
    family(cat,[f'查{n}2024年营业收入，并用{y}年年报解释{y}年收入变化；没有旧报告就保留2024年数字并说明。',f'请给{n}2024年营收，另外只查{y}年年报中的收入变动原因，缺报告不要换年。',f'{n}2024年收入是多少？再找{y}年度收入变化原因；没有那份年报请说明，保留能核实的数字。'],
           expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit='万元'),goal('cause',[c],['operating_revenue'],[y],completion='missing_report'),allow_expected_partial=True))

    for i in range(8):
        a=choose(i); b=choose(i+3); c=choose(i+5); na,nb,nc=companies[a],companies[b],companies[c]
        metric='operating_revenue'; unit='万元'; year=2023; scope='consolidated'; period='FY'
        if i==6: period='HY'
        if i==7: scope='parent'
        start=goal(codes=[a,b] if i in {1,2} else [a],metrics=[metric],years=[year],scope=scope,periods=[period],unit=unit,decimals=2,format='table')
        q=f'查询{na}'+(f'和{nb}' if i in {1,2} else '')+f'{year}年'+('半年累计' if period=='HY' else '年报')+('母公司' if scope=='parent' else '合并')+'营业收入，万元两位小数，只给表格。'
        turns=[(q,expected(start,no_chart=True))]; new=dict(start)
        if i==0:q2=f'把公司换成{nb}，其他不变。';new['codes']=[b]
        elif i==1:q2=f'去掉{nb}，只保留{na}。';new['codes']=[a]
        elif i==2:q2=f'再加上{nc}，其余沿用。';new['codes']=[a,b,c]
        elif i==3:q2='改查归母净利润，其他不变。';new['metrics']=['attributable_net_profit']
        elif i==4:q2='改成2024年，其他不变。';new['years']=[2024]
        elif i==5:q2='金额改成亿元，保留四位小数。';new.update(unit='亿元',decimals=4)
        elif i==6:q2='改成年报全年累计，年份不变。';new['periods']=['FY']
        else:q2=f'公司换成{nb}，母公司口径和其他条件不变。';new['codes']=[b]
        turns.append((q2,expected(goal(**new),no_chart=True)))
        new2=dict(new);new2['years']=[2024]
        turns.append(('现在查询2024年，其他条件不变。',expected(goal(**new2),no_chart=True)))
        new3=dict(new2);new3.update(unit='亿元',decimals=3)
        turns.append(('用亿元三位小数显示，仍只给表格。',expected(goal(**new3),no_chart=True)))
        group('公司指标年份修改',turns)

    for i in range(6):
        c=choose(i+2); n=companies[c]; m=['operating_revenue','attributable_net_profit','total_assets'][i%3]
        g=goal(codes=[c],metrics=[m],years=[2023],unit='万元',decimals=2,format='table')
        cm=['gross_margin','eps_basic','attributable_net_profit'][i%3]
        group('概念插话恢复',[(f'查询{n}2023年{LABELS[m]}，万元两位小数，只给表格。',expected(g,no_chart=True)),
            (f'先解释一下{LABELS[cm]}是什么意思，不查数字。',expected(goal('concept',metrics=[cm],concept_mode='definition'),mode='concept',no_query=True)),
            ('回到刚才的财务查询，改成2024年，其他不变。',expected(goal(**{**g,'years':[2024]}),no_chart=True)),
            ('还是这些条件，改用亿元三位小数显示。',expected(goal(**{**g,'years':[2024],'unit':'亿元','decimals':3}),no_chart=True))])

    for i in range(6):
        c=choose(i+1,'attributable_net_profit'); b=choose(i+5,'attributable_net_profit'); n=companies[c]
        g=goal(codes=[c],metrics=['attributable_net_profit'],years=[2024],unit='万元')
        group('事实指代原页',[(f'查询{n}2024年归母净利润，用万元。',expected(g)),
            ('这个数表示盈利还是亏损？不要重复金额。',expected(goal(**{**g,'kind':'sign'}),mode='reference',no_repeat=True)),
            ('它在原件哪一页哪一行？只给位置，不重新查数，不重复金额。',expected(goal(**{**g,'kind':'quote','quote_mode':'location'}),mode='reference',no_query=True,no_repeat=True)),
            (f'现在换成{companies[b]}，查询2024年归母净利润，继续用万元。',expected(goal(**{**g,'codes':[b]})))])

    for i in range(4):
        c=choose(i+4); n=companies[c]; m=['operating_revenue','total_assets','total_liabilities','attributable_net_profit'][i]
        first=goal(metrics=[m],years=[2024]); full=goal(codes=[c],metrics=[m],years=[2024],unit='万元',decimals=2)
        group('澄清后继续',[(f'我想查2024年{LABELS[m]}，还没指定公司，请先问我。',expected(first,mode='clarify',known_metrics=[m],known_years=[2024])),
            (f'{n}，用万元，两位小数。',expected(full)),
            ('换成2023年，其他不变。',expected(goal(**{**full,'years':[2023]}))),
            ('只给表格，不画图。',expected(goal(**{**full,'years':[2023],'format':'table'}),no_chart=True))])

    for i in range(4):
        c=choose(i+3); n=companies[c]; y=missing(c)
        reliable=goal(codes=[c],metrics=['operating_revenue'],years=[2023],unit='万元')
        absent=goal('cause',[c],['operating_revenue'],[y],completion='missing_report')
        group('复合目标部分证据缺失',[(f'查{n}2023年营业收入，再找{y}年年报中的{y}年收入变化原因；缺报告请说明并保留数字。',expected(reliable,absent,allow_expected_partial=True)),
            ('数字改查2024年，原因仍只找'+str(y)+'年年报，缺原件不要替换。',expected(goal(**{**reliable,'years':[2024]}),absent,allow_expected_partial=True)),
            ('先解释毛利率，不查询公司数字。',expected(goal('concept',metrics=['gross_margin'],concept_mode='definition'),mode='concept',no_query=True)),
            ('回到刚才的公司，只查2024年营业收入，不再解释原因，用万元。',expected(goal(**{**reliable,'years':[2024]})))])

    for i in range(2):
        c=choose(i+6); b=choose(i+8); n=companies[c]
        group('话题清空',[(f'查{n}2024年营业收入，万元。',expected(goal(codes=[c],metrics=['operating_revenue'],years=[2024],unit='万元'))),
            ('换个话题，不再沿用公司。解释毛利率概念，不查数字。',expected(goal('concept',metrics=['gross_margin'],concept_mode='definition'),mode='concept',no_query=True,clear_company=True)),
            ('它2023年营业收入多少？',expected(goal(metrics=['operating_revenue'],years=[2023]),mode='clarify',known_metrics=['operating_revenue'],known_years=[2023],clear_company=True,no_old_fact=True)),
            (f'{companies[b]}，用万元两位小数。',expected(goal(codes=[b],metrics=['operating_revenue'],years=[2023],unit='万元',decimals=2)))])

    assert Counter(f['category'] for f in families)==Counter({k:v[0] for k,v in CATEGORIES.items()})
    assert Counter(g['category'] for g in groups)==Counter({k:v[0] for k,v in DIALOGUES.items()})
    rng=random.Random(seed); sealed_f=set();sealed_g=set()
    for category,(_,count) in CATEGORIES.items():
        selected=[f['id'] for f in families if f['category']==category];rng.shuffle(selected);sealed_f.update(selected[:count])
    for category,(_,count) in DIALOGUES.items():
        selected=[g['id'] for g in groups if g['category']==category];rng.shuffle(selected);sealed_g.update(selected[:count])
    cases=[]
    for family_item in families:
        for variant,q in enumerate(family_item['questions'],1):
            cases.append(dict(id=f"{family_item['id']}V{variant}",family=family_item['id'],variant=variant,
                category=family_item['category'],group=None,question=q,expect=family_item['expect'],
                partition='sealed' if family_item['id'] in sealed_f else 'development'))
    for group_item in groups:
        for turn,(q,e) in enumerate(group_item['turns'],1):
            cases.append(dict(id=f"{group_item['id']}T{turn}",family=None,category=group_item['category'],
                group=group_item['id'],turn=turn,question=q,expect=e,
                depends_on=[f"{group_item['id']}T{j}" for j in range(1,turn)],
                partition='sealed' if group_item['id'] in sealed_g else 'development'))
    assert len(cases)==300 and Counter(c['partition'] for c in cases)==Counter(development=240,sealed=60)
    assert len({re.sub(r'\s','',c['question']) for c in cases if not c['group']})==180
    for case in cases:
        for e in case['expect']['goal_expectations']:
            if e['kind'] in {'concept'} or case['expect']['kind'] in {'clarify','unsupported'} or e.get('completion')=='missing_report':continue
            for c in e['codes']:
                for y in e['years']:
                    for p in e['periods']:
                        for m in e['metrics']:assert (c,y,p,m,e['scope']) in identities,(case['id'],c,y,p,m,e['scope'])
    observed={c for case in cases for e in case['expect']['goal_expectations'] for c in e['codes']}
    assert observed==set(codes)
    return cases


def oracle_suite(suite):
    cases=[]
    for case in suite['cases']:
        for i,g in enumerate(case['expect']['goal_expectations']):
            if g['kind']=='concept' or case['expect']['kind'] in {'clarify','unsupported'} or g.get('completion')=='missing_report':continue
            e=dict(g,kind='financial',goals=[g['kind']])
            cases.append(dict(id=case['id']+'G'+str(i),expect=e))
    return dict(version=3,source_data_version=suite['source_data_version'],cases=cases)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--audit',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--seed',type=int,default=20261003);args=parser.parse_args()
    from eval.real200_acceptance import reference,prepare
    folder=Path(args.output).resolve();assert folder.is_relative_to(ROOT.resolve())
    if folder.exists():raise FileExistsError('Existing suite must never be overwritten')
    ref=reference(Path(args.audit).resolve());cases=build_cases(ref,args.seed)
    suite=dict(version=3,name='Common financial questions 300',source_data_version=ref['version'],
        reference_date='2026-10-03',seed=args.seed,classification='assistant-authored fixed common questions; not third-party testing',
        no_accuracy_threshold=True,cases=cases)
    folder.mkdir(parents=True)
    for name,selected in [('development240',[c for c in cases if c['partition']=='development']),
                          ('sealed60',[c for c in cases if c['partition']=='sealed']),('common300',cases)]:
        dest=folder/name;dest.mkdir();current={**suite,'name':name,'cases':selected}
        (dest/'cases.json').write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary=dest/'oracle-input.json';temporary.write_text(json.dumps(oracle_suite(current),ensure_ascii=False,indent=2),encoding='utf-8')
        prepare(oracle_suite(current),ref,dest/'oracle.json')
        oracle=json.loads((dest/'oracle.json').read_text(encoding='utf-8'))
        oracle['suite_sha256']=hashlib.sha256(json.dumps(current,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        oracle['reference_suite_sha256']=digest(oracle_suite(current))
        narrative=[]
        from pypdf import PdfReader
        for case in selected:
            for g in case['expect']['goal_expectations']:
                if proof:=g.get('narrative'):
                    f=next(f for f in ref['facts'].values() if (f['stock_code'],f['year'],f['period'],f['metric'],f['scope'])==(proof['stock_code'],proof['year'],'FY',proof['metric'],'consolidated'))
                    source=f['source'];pdf=ROOT/source['source_path'];assert hashlib.sha256(pdf.read_bytes()).hexdigest()==source['source_sha256']
                    page_text=PdfReader(pdf).pages[proof['physical_page']-1].extract_text() or ''
                    for term in proof['terms']:assert re.sub(r'\s','',term) in re.sub(r'\s','',page_text)
                    narrative.append(dict(case_id=case['id'],**proof,source_path=source['source_path'],source_sha256=source['source_sha256'],original_page=page_text))
        oracle['narrative_pages']=narrative
        (dest/'oracle.json').write_text(json.dumps(oracle,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=dict(version=3,source_data_version=ref['version'],seed=args.seed,counts=dict(total=300,development=240,sealed=60,singles=180,dialogue_turns=120),
        categories={k:dict(families=v[0],sealed_families=v[1]) for k,v in CATEGORIES.items()},
        dialogues={k:dict(groups=v[0],sealed_groups=v[1]) for k,v in DIALOGUES.items()},
        files={str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*.json')},
        sealed_policy='Do not run/read sealed60 or full common300 until the two bounded development adjustments have ended and final source is frozen')
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts=manifest['counts'],data_version=ref['version'],manifest_sha256=hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest()),ensure_ascii=False))


if __name__=='__main__':main()
