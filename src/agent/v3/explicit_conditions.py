"""词面观察提供候选；模型提案中的业务条件由编译和核验检查，不二次改写。"""
from __future__ import annotations
import re
from .request_bindings import metric_candidates, positive_period_mentions, explicit_output_unit, explicit_presentation_bindings, is_negated_span, explicit_restrictions

def explicit_scope_conflicts(question):
    """同一查询明确要求互斥范围时保留冲突；概念中的多个口径不是查询冲突。"""
    financial_span=re.split(r'(?:再|然后|另外|并)(?:查(?:询)?|看)',question)[-1]
    parent=bool(re.search(r'母公司(?:单体|口径|营业收入|净利润|利润表|报表|的?归母|的?扣非)',financial_span))
    attributed=bool(re.search(r'归母|归属于(?:上市公司|母公司)(?:所有者|股东)',financial_span))
    conflicts=[]
    if parent and attributed:
        conflicts.append('母公司单体口径与归母净利润口径不同；请确认查母公司净利润，还是合并报表归母净利润。')
    exclusive=bool(re.search(r'仅|只|必须|不(?:要|接受).*拆',financial_span))
    consolidated=any(not is_negated_span(financial_span,m.start(),m.end()) and not re.search(r'不(?:接受|采用|使用)\s*$',financial_span[:m.start()])
        for m in re.finditer(r'合并(?:口径|报表)',financial_span))
    if parent and consolidated and exclusive:
        conflicts.append('同一项查询不能同时仅采用合并口径和仅采用母公司口径；请明确选择，或允许分别查询。')
    return conflicts

def explicit_conditions(question, *, concept=False):
    """候选不是最终选择；短别名、口径与计算不再作为自动覆盖值。"""
    output=explicit_presentation_bindings(question)
    if unit:=explicit_output_unit(question):output['presentation.unit']=unit
    candidates=metric_candidates(question,positive_only=True,concept=concept)
    if candidates:output['candidate_metrics']=sorted(candidates)
    periods={period for _,_,period in positive_period_mentions(question)}
    if periods:output['observed_periods']=sorted(periods)
    output.update({'restrictions.'+field:value for field,value in explicit_restrictions(question).items()})
    return output

def bind_explicit(plan,question,companies=None):
    """兼容历史调用名；目标条件不由另一个词面解析器覆盖。"""
    return plan.model_copy(deep=True),{}
