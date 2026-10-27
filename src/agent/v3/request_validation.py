"""核对编译后的合同，不用另一份词句规则重新选择目标、公司或口径。"""
from .catalog import METRICS
from .units import presentation_unit_compatible


def review_contract(request, semantic):
    planned = {goal.id: goal for goal in request.goals}
    numeric = {'lookup', 'compare', 'rank'}
    financial = numeric | {'chart', 'quote', 'cause', 'sign'}

    def reject(field, detail):
        setattr(semantic, field, False)
        if detail not in semantic.planner_defects:
            semantic.planner_defects.append(detail)

    for requirement in semantic.goal_requirements:
        kinds = {planned[identifier].kind for identifier in requirement.goal_ids if identifier in planned}
        compatible = set(numeric) if requirement.kind in numeric else {requirement.kind}
        if requirement.kind == 'lookup': compatible |= {'chart', 'cause', 'sign'}
        if requirement.kind == 'other': compatible = {'unsupported'}
        represented = bool(requirement.goal_ids) and set(requirement.goal_ids) <= set(planned)
        if represented and all(planned[identifier].clarification for identifier in requirement.goal_ids) and kinds & compatible:
            requirement.covered=True
        if not represented or not kinds & compatible or not requirement.covered:
            requirement.covered = False
            reject('goals_correct', '目标要求没有关联到实际计划。')
    declared = {identifier for requirement in semantic.goal_requirements for identifier in requirement.goal_ids}
    if set(planned) - declared:
        reject('goals_correct', '计划存在未登记的额外目标。')
    for goal in request.goals:
        c = request.for_goal(goal).conditions
        waiting = bool(goal.clarification)
        if goal.intent_source and goal.intent_source not in request.question:
            reject('goals_correct', '目标依据不在本轮原话中。')
        if not waiting and (set(c.codes) & set(c.restrictions.excluded_codes) or set(c.metrics) & set(c.restrictions.excluded_metrics)):
            reject('constraints_correct', '有效选择与排除条件冲突。')
        if any(metric not in METRICS for metric in c.metrics):
            reject('metrics_and_scope_correct', '指标不在登记目录中。')
        if goal.kind in financial and not waiting:
            if not c.codes and not c.all_companies and not c.restrictions.no_query:
                reject('companies_correct', '可执行财务目标缺少公司，必须先澄清。')
            if not c.metrics:
                reject('metrics_and_scope_correct', '可执行财务目标缺少指标。')
            if c.scope == 'parent' and any('parent' not in METRICS[metric].scopes for metric in c.metrics if metric in METRICS):
                reject('metrics_and_scope_correct', '该指标不适用于母公司报表。')
            if any(not presentation_unit_compatible(c, metric) for metric in c.metrics if metric in METRICS):
                reject('presentation_correct', '指标维度与输出单位不兼容。')
            if c.time.mode == 'explicit' and not c.time.years and not c.time.pairs:
                reject('time_correct', '明确期间目标缺少报告年份。')
            if c.calculation != 'none' and c.comparison_axis == 'none':
                reject('goals_correct', '计算目标缺少明确比较轴。')
            if c.calculation_inputs and (c.calculation == 'none' or any(
                    operand.code and operand.code not in c.codes
                    for pair in c.calculation_inputs for operand in (pair.current, pair.base))):
                reject('goals_correct', '计算输入不属于本目标的公司或缺少对应运算。')
            if c.restrictions.no_query and goal.kind in numeric | {'chart', 'cause'}:
                reject('constraints_correct', '查询型目标与禁止取数冲突；来源追问须使用事实引用目标。')
        if not waiting and goal.kind == 'chart' and c.restrictions.no_chart:
            reject('presentation_correct', '绘图目标违反已编译的不画图条件。')
        if goal.kind == 'concept' and goal.concept_mode == 'difference' and len(set(c.metrics)) < 2 and not waiting:
            reject('goals_correct', '概念区别目标需要两个明确指标。')
    if semantic.planner_defects:
        if 'conditions' not in semantic.repair_domains: semantic.repair_domains.append('conditions')
        semantic.satisfied = False
    return semantic
