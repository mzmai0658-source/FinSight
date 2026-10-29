"""从报告前部原文识别公司和期间，不依赖上传文件名。"""
import re


def original_report_identity(report,text):
    compact=re.sub(r'\s+','',text)
    # 保留代码两侧空白，防止下一个数字字段抹去边界；公司简介中的任意六位数不是证券代码。
    codes=re.findall(r'(?:证券|股票|公司)\s*代码\s*[:：]?\s*(\d{6})(?!\d)',text)
    company_ok=report['stock_code'] in codes if codes else report['company'] in compact
    labels={'FY':('年度报告',),'HY':('半年度报告','半年报'),'Q1':('第一季度报告','一季度报告'),'Q3':('第三季度报告','三季度报告')}
    period_ok=any(re.search(str(report['year'])+r'(?:年|年度)?'+re.escape(label),compact) for label in labels[report['period']])
    return dict(company=company_ok,period=period_ok,codes_found=sorted(set(codes)),
        identity_basis='explicit_security_code' if codes else 'registered_company_literal')
