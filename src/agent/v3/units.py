"""区分输入事实单位与请求计算结果单位，避免金额换算影响每股指标和比率。"""
from .catalog import METRICS

PERCENT_CALCULATIONS={'yoy','relative_percent'}


def fact_output_unit(conditions,source_unit,metric=None):
    selected=conditions.presentation.metric_units.get(metric) if metric else None
    explicit_metric=selected is not None
    if selected is None:selected=conditions.presentation.unit
    if conditions.calculation in PERCENT_CALCULATIONS and selected=='%' and source_unit!='%':return None
    if not explicit_metric and len({METRICS[m].dimension for m in conditions.metrics if m in METRICS})>1:
        if source_unit!='元' and selected in {'元','万元','亿元'}:return None
        if source_unit=='元' and selected in {'%','元/股'}:return None
    return selected


def result_output_unit(conditions,source_unit,metric=None):
    if source_unit in {'%','百分点'}:return source_unit
    return fact_output_unit(conditions,source_unit,metric)


def presentation_unit_compatible(conditions,metric):
    selected=fact_output_unit(conditions,METRICS[metric].unit,metric)
    if not selected:return True
    if conditions.calculation in PERCENT_CALCULATIONS and selected=='%':return True
    definition=METRICS[metric]
    return selected in {'元','万元','亿元'} if definition.dimension=='money' else selected==definition.unit
