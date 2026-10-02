"""作品说明：区分输入事实单位与请求计算结果单位，避免金额换算影响每股指标和比率。"""
from .catalog import METRICS

PERCENT_CALCULATIONS={'yoy','relative_percent'}


def fact_output_unit(conditions,source_unit):
    if conditions.calculation in PERCENT_CALCULATIONS and conditions.presentation.unit=='%':return None
    return conditions.presentation.unit


def presentation_unit_compatible(conditions,metric):
    selected=conditions.presentation.unit
    if not selected:return True
    if conditions.calculation in PERCENT_CALCULATIONS and selected=='%':return True
    definition=METRICS[metric]
    return selected in {'元','万元','亿元'} if definition.dimension=='money' else selected==definition.unit
