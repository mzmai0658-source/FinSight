"""作品说明：从报告前部原文识别公司和期间，不依赖上传文件名。"""
import re


def original_report_identity(report,text):
    compact=re.sub(r'\s+','',text)
    codes=re.findall(r'(?:证券|股票)代码[:：]?(\d{6})',compact)
    # 作品说明：公司简介中的股票代码标题和值可能被交易所、名称等其他标题隔开。
    for heading in re.finditer(r'股票代码',compact):
        segment=compact[heading.end():heading.end()+140]
        if report['company'] in segment:
            codes.extend(re.findall(r'(?<!\d)\d{6}(?!\d)',segment))
    company_ok=report['stock_code'] in codes if codes else report['company'] in compact
    labels={'FY':('年度报告',),'HY':('半年度报告','半年报'),'Q1':('第一季度报告','一季度报告'),'Q3':('第三季度报告','三季度报告')}
    period_ok=any(re.search(str(report['year'])+r'(?:年|年度)?'+re.escape(label),compact) for label in labels[report['period']])
    return dict(company=company_ok,period=period_ok,codes_found=sorted(set(codes)),
        identity_basis='explicit_security_code' if codes else 'registered_company_literal')
