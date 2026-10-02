"""作品说明：共享和目标条件修改完成后统一补充领域默认值，使默认规则依据最终选择生效。"""
from .contracts import Origin


def finalize_conditions(conditions, kinds, turn_id):
    c = conditions.model_copy(deep=True)
    mode_origin = c.origins.get('time.mode')
    if c.time.mode == 'latest' or (
        c.time.mode in {'latest_each', 'latest_common'}
        and 'time_policy' in c.origins
        and (mode_origin is None or mode_origin.kind == 'default')
    ):
        c.time.mode = 'latest_common' if set(kinds) & {'compare', 'rank'} else 'latest_each'
        c.origins['time_policy'] = Origin(kind='default',
            text='比较与排名取共同最新；逐家查询取各自最新', turn_id=turn_id)
    axis_origin = c.origins.get('comparison_axis')
    if (c.calculation != 'none' or 'compare' in kinds) and (
        c.comparison_axis == 'none' or axis_origin is not None and axis_origin.kind == 'default'
    ):
        # 作品说明：公司集合或局部条件变化后重算派生默认值，保留用户明确指定的方向。
        pairs = c.time.pairs or [(year, period) for year in c.time.years for period in c.time.periods]
        c.comparison_axis = 'none'
        if len(c.codes) > 1 and (len(pairs) == 1 or
            not pairs and c.time.span in {None, 1} and len(c.time.periods) == 1):
            c.comparison_axis = 'companies'
        elif len(c.codes) == 1 and (len(pairs) > 1 or (c.time.span or 0) > 1 or c.calculation == 'yoy'):
            c.comparison_axis = 'years'
        c.origins['comparison_axis'] = Origin(kind='default',
            text='按明确比较对象判断：同公司跨期或同期跨公司', turn_id=turn_id)
    if 'scope' not in c.origins:
        c.origins['scope'] = Origin(kind='default', text='普通财务金额默认合并口径', turn_id=turn_id)
    if c.presentation.format == 'table':
        c.restrictions.no_chart = True
    return c
