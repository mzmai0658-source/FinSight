"""作品说明：共享条件和分目标例外共用同一套修改规则，避免两处对编辑操作产生不同解释。"""
from datetime import date
import re


def mentioned_years(question):
    normalized=''.join(question.split())
    years={int(value) for value in re.findall(r'(?<!\d)(20\d{2})(?!\d)',normalized)}
    years.update(2000+int(value) for value in re.findall(r'(?<!\d)(\d{2})(?=年)',normalized))
    if re.fullmatch(r'\d{2}',normalized):years.add(2000+int(normalized))
    for marker,offset in (('今年',0),('本年',0),('去年',1),('前年',2)):
        if marker in normalized:years.add(date.today().year-offset)
    if len(years)>=2 and re.search(r'(?:至|到|[-—~～])',normalized):
        years.update(range(min(years),max(years)+1))
    return years


def validate_year_change(question,old,change):
    from .state import InvalidUnderstanding
    supplied=change.get('years')
    if supplied is None or supplied==old.get('years'):return
    # 作品说明：已确认年份可沿用；新增年份须有本轮文字或明确的相对时间依据。
    if supplied and set(supplied)<=mentioned_years(question)|set(old.get('years') or []):return
    if not supplied and change.get('mode') in {'latest','latest_each','latest_common','calendar_years'} and any(
        word in question for word in ('最新','最近','近','过去','自然年')):return
    raise InvalidUnderstanding('本轮没有明确修改这些年份，不能用today或重新猜测覆盖已确认年份。')


def edit_value(previous,operation,value,default):
    if operation=='inherit':return previous
    if operation=='clear':return default
    if operation=='replace':return value
    if not isinstance(previous,list) or not isinstance(value,list):raise ValueError('Set edits require lists')
    if operation=='add':return list(dict.fromkeys([*previous,*value]))
    if operation=='remove':return [item for item in previous if item not in value]
    if operation=='keep':return list(dict.fromkeys(value))
    raise ValueError('Unknown edit operation')


def apply_edits(base,edits,question,turn_id=''):
    """作品说明：统一处理对话与分目标条件迁移；相关时间字段一起归一化，先合并再验证，保留不支持的单季请求身份。"""
    from .contracts import Conditions,Origin,SET_PATHS
    raw=base.model_dump();defaults=Conditions().model_dump();time_patch={}
    assigned=set()
    for edit in edits:
        if edit.operation=='inherit':continue
        if edit.operation in {'replace','clear','keep'}:
            if edit.field in assigned:raise ValueError('一个字段只能有一项最终替换；多公司应一次写完整集合或分别给目标例外，不能让后一次替换静默覆盖前一次。')
            assigned.add(edit.field)
        if edit.text and edit.text not in question:raise ValueError('Edit provenance must be a literal current-input span')
        path=edit.field.split('.');target=raw;default=defaults
        for prefix in path[:-1]:target=target[prefix];default=default[prefix]
        key=path[-1]
        value=patch_values(edit.field,edit.value) if hasattr(edit.value,'model_dump') else edit.value
        if edit.field=='metrics' and edit.operation in {'replace','add','keep'}:
            from .request_bindings import metric_candidates
            candidates=metric_candidates(edit.text or question)
            if candidates and set(value)-candidates:
                raise ValueError('所选指标与这项编辑的明确指标原话不等价；不能以主营业务收入替代营业收入或以相近指标代替。')
        if edit.field in {'time','presentation','restrictions'} and edit.operation!='clear':
            if not isinstance(value,dict):raise ValueError('Object field requires an object')
            if edit.field=='time':time_patch.update(value)
            else:target[key]=merge_object(edit.field,target[key],value)
        else:
            if edit.operation in {'add','remove','keep'} and edit.field not in SET_PATHS:
                raise ValueError('Set operations apply only to registered set conditions')
            previous=time_patch.get(key,target[key]) if path[0]=='time' and len(path)==2 else target[key]
            updated=edit_value(previous,edit.operation,value,default[key])
            if path[0]=='time' and len(path)==2:time_patch[key]=updated
            else:target[key]=updated
            if edit.field in {'codes','metrics'}:
                exclusion='excluded_codes' if edit.field=='codes' else 'excluded_metrics'
                excluded=raw['restrictions'][exclusion]
                if edit.operation in {'remove','keep'}:
                    removed=value if edit.operation=='remove' else [item for item in previous if item not in updated]
                    excluded=list(dict.fromkeys([*excluded,*removed]))
                if edit.operation in {'replace','add','keep'}:excluded=[item for item in excluded if item not in updated]
                raw['restrictions'][exclusion]=excluded
                if edit.field=='codes' and edit.operation in {'replace','keep','clear'}:raw['all_companies']=False
        origin=Origin(kind='current',text=edit.text or question,turn_id=turn_id).model_dump()
        raw['origins'][edit.field]=origin
        if edit.field in {'time','presentation','restrictions'} and isinstance(value,dict):
            for subfield in value:raw['origins'][edit.field+'.'+subfield]=origin
    if time_patch:
        validate_year_change(question,raw['time'],time_patch)
        validate_time_policy_change(question,raw['time'],time_patch)
        raw['time']=merge_object('time',raw['time'],time_patch)
    if raw['calculation']=='yoy' and any(metric.endswith('_reported_yoy') or metric.endswith('_reported_change_vs_year_end') for metric in raw['metrics']):
        raise ValueError('计算同比须选择基础金额指标，不能再计算披露增长率的增长率。')
    return Conditions.model_validate(raw)


def validate_time_policy_change(question,previous,patch):
    from .request_bindings import positive_period_mentions,positive_latest_mentions
    mode=patch.get('mode')
    if mode in {'latest','latest_each','latest_common'} and mode!=previous['mode'] and (
        previous.get('years') or previous.get('pairs') or previous['mode']!='latest'
    ) and not positive_latest_mentions(question):
        raise ValueError('报告期修改没有明确要求最新报告，不能清除已确认年份。')
    if mode=='calendar_years' and mode!=previous['mode'] and '自然年' not in question:
        raise ValueError('自然年策略必须有本轮自然年要求，不能替代已选报告期。')
    if 'periods' in patch and patch['periods']!=previous['periods'] and patch['periods']:
        named={period for _,_,period in positive_period_mentions(question)}
        if not named or set(patch['periods'])!=named:
            raise ValueError('累计报告期与本轮明确报告名称不一致，不能把年报保留成半年报。')

def patch_values(field, patch):
    nullable={'time':{'span','pairs'},'presentation':{'unit','decimals','limit'}}.get(field,set())
    return {key:value for key,value in patch.model_dump(exclude_unset=True).items() if value is not None or key in nullable}

def merge_object(field, previous, patch):
    value={**previous,**patch}
    if field=='time':
        if patch.get('years') and patch.get('mode') in {'latest','latest_each','latest_common'}:
            raise ValueError('Explicit year values and latest-period mode are contradictory; state the requested policy rather than discarding the years')
        if patch.get('years') and 'mode' not in patch: value['mode']='explicit'
        if any(key in patch for key in ('years','periods')) and 'pairs' not in patch: value['pairs']=None
        if patch.get('mode') in {'latest','latest_each','latest_common'}:
            value['years']=[]; value['pairs']=None
            if 'span' not in patch: value['span']=None
        if patch.get('mode')=='calendar_years' and value.get('span'):
            # 作品说明：相对自然年窗口以当前时钟确定，不借模型提供或旧任务留下的年份清单定位。
            value['years']=[]; value['pairs']=None
        if patch.get('single_quarter') is False: value['quarters']=[]
    return value
