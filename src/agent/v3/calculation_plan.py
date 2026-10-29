"""从已编译条件解析计算输入的本期与基期身份，不重新解析用户句子或生成答案。"""
def matches(operand, code, year, period):
    return ((operand.code is None or operand.code == code) and
        (operand.year is None or operand.year == year) and
        (operand.period is None or operand.period == period))


def target_periods(conditions, selections):
    if not conditions.calculation_inputs:
        return selections
    return {code: [pair for pair in pairs if any(matches(item.current, code, *pair)
        for item in conditions.calculation_inputs)] for code, pairs in selections.items()}


def support_periods(conditions, selections):
    targets=target_periods(conditions,selections)
    if not conditions.calculation_inputs:
        return {code: sorted({(year-1,period) for year,period in pairs}) for code,pairs in targets.items()} if conditions.calculation=='yoy' else {}
    support={code:set() for code in conditions.codes}
    for code,pairs in targets.items():
        for year,period in pairs:
            for item in conditions.calculation_inputs:
                if not matches(item.current,code,year,period):continue
                base=item.base
                base_code=base.code or code
                if base_code not in support:continue
                base_year=base.year if base.year is not None else year
                base_period=base.period or period
                support[base_code].add((base_year,base_period))
    return {code:sorted(pairs) for code,pairs in support.items() if pairs}


def explicit_pairs(conditions, values):
    """同一指标分组中每个角色必须唯一，含糊或缺失不能凭大小挑一个。"""
    pairs=[]
    for item in conditions.calculation_inputs:
        current=[fact for fact in values if matches(item.current,fact.stock_code,fact.year,fact.period)]
        base=[fact for fact in values if matches(item.base,fact.stock_code,fact.year,fact.period)]
        if len(current)!=1 or len(base)!=1 or current[0].id==base[0].id:continue
        pairs.append((current[0],base[0]) if conditions.comparison_axis=='companies' else (base[0],current[0]))
    return list({(a.id,b.id):(a,b) for a,b in pairs}.values())


def value_order_requested(request):
    c=request.conditions
    if any(goal.kind=='rank' for goal in request.goals):return True
    return (any(goal.kind in {'lookup','compare'} for goal in request.goals) and
        (c.presentation.limit is not None or 'presentation.order' in c.origins))


def input_plan_errors(conditions,selections):
    errors=[]
    for item in conditions.calculation_inputs:
        if conditions.calculation=='none':errors.append('calculation_inputs_without_operation')
        if any(operand.code and operand.code not in conditions.codes for operand in (item.current,item.base)):
            errors.append('calculation_company_outside_selection')
        if not any(matches(item.current,code,*pair) for code,pairs in selections.items() for pair in pairs):
            errors.append('calculation_target_outside_selection')
    return sorted(set(errors))


def expected_input_pairs(conditions,selections):
    return sum(1 for item in conditions.calculation_inputs for code,pairs in selections.items()
        for pair in pairs if matches(item.current,code,*pair))
