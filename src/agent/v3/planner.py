"""作品说明：模型一次提出请求，再编译为统一v3合同。"""
from __future__ import annotations
from datetime import date
import hashlib
import json
import re
from typing import Literal
from pydantic import Field,model_validator

from .catalog import ALIASES,catalog_for_prompt
from .contracts import Check, DialogueState, GoalKind, Strict, Understanding
from .model import AsyncModel, Budget
from .proposals import IntentPlan,ConditionPlan,combine_plans,compile_proposal


class GoalCoverage(Strict):
    source_ref: str = ''
    kind: GoalKind | Literal['greeting','other']
    goal_ids: list[str]
    representation: Literal['represented','missing'] = Field(description='Whether the request has a goal for this requirement. This audit runs before execution; empty facts do not make a represented goal missing.')

    @model_validator(mode='before')
    @classmethod
    def read_legacy_flags(cls,value):
        if isinstance(value,dict):
            value=dict(value)
            for key in ('planned','covered'):
                if key in value:
                    legacy=value.pop(key)
                    if not isinstance(legacy,bool):raise ValueError('Legacy coverage flags must be boolean')
                    representation='represented' if legacy else 'missing'
                    if value.get('representation',representation)!=representation:raise ValueError('Conflicting coverage representations')
                    value['representation']=representation
        return value

    @property
    def covered(self):return self.representation=='represented'

    @covered.setter
    def covered(self,value):self.representation='represented' if value else 'missing'


class SemanticReview(Strict):
    audit_mode: Literal['model','program'] = 'model'
    goal_requirements: list[GoalCoverage] = Field(min_length=1)
    satisfied: bool
    clarification: list[str]
    planner_defects: list[str]
    companies_correct: bool
    metrics_and_scope_correct: bool
    time_correct: bool
    goals_correct: bool
    constraints_correct: bool
    presentation_correct: bool
    continuity_correct: bool = True
    repair_domains: list[Literal['intent','conditions']] = Field(default_factory=list)

    @property
    def checks(self):
        return [Check(name=('explicit_' if self.audit_mode=='program' else 'semantic_')+key,status='pass' if getattr(self,key) and self.satisfied and not self.planner_defects else 'unknown',
            detail=('程序检查显式条件与编辑结果；自由语义未由独立模型认证' if self.audit_mode=='program' else '对照原话与确认上下文') if getattr(self,key) else '；'.join(self.planner_defects or self.clarification))
            for key in ('companies_correct','metrics_and_scope_correct','time_correct','goals_correct','constraints_correct','presentation_correct','continuity_correct')]

    @property
    def accepted(self):
        return self.satisfied and not self.planner_defects and all(item.covered for item in self.goal_requirements) and all(getattr(self,key) for key in
            ('companies_correct','metrics_and_scope_correct','time_correct','goals_correct','constraints_correct','presentation_correct','continuity_correct'))


def planner_state(state: DialogueState):
    """作品说明：规划上下文只包含确认过的结构化记忆，减少重复原问题和财务正文的干扰。"""
    def conditions(value):
        if value is None: return None
        raw=value.model_dump(mode='json')
        raw['origins']={key:{'kind':origin.kind} for key,origin in value.origins.items()}
        return raw
    def execution(value):
        if value is None: return None
        return dict(turn_id=value.turn_id,status=value.status,time_rule=value.time_rule,
            resolved_periods=value.resolved_periods,conditions=conditions(value.conditions),
            verified_fact_count=len(value.fact_refs),goal_conditions={key:conditions(c) for key,c in value.goal_conditions.items()})
    def goals(values):
        # 作品说明：待澄清内容放入pending.clarification；旧目标阻塞信息不作为本轮新要求输入。
        return [{key:value for key,value in g.model_dump(mode='json').items()
                 if key in {'id','kind','catalog_target','quote_mode','concept_mode','execution_ref'}} for g in values]
    from .state import financial_context_conditions
    return dict(version=3,topic=state.topic,legacy_scope_unknown=state.legacy_scope_unknown,
        conditions=conditions(state.conditions),active_goals=goals(state.active_goals),suspended=conditions(state.suspended),
        goal_conditions={key:conditions(value) for key,value in financial_context_conditions(state).items()},
        pending={'question':state.pending.question,'clarification':state.pending.clarification,
            'conditions':conditions(state.pending.conditions),'choices':[choice.model_dump() for choice in state.pending.clarification_choices],
            'goals':goals(state.pending.goals)} if state.pending else None,
        last_execution=execution(state.last_execution),executions=[dict(turn_id=e.turn_id,status=e.status,
            time_rule=e.time_rule,resolved_periods=e.resolved_periods) for e in state.executions],
        interrupted_request=state.interrupted_request.model_dump(mode='json') if state.interrupted_request else None,
        recent_fact_count=len(state.recent_facts)+len(state.recent_computed),
        recent_values=[dict(kind='calculation',metric=ref.metric,scope=ref.scope,company=ref.company,year=ref.year,period=ref.period,calculation=ref.calculation)
            for ref in state.recent_computed])


from .prompts import TURN


def confirmed_task_view(state: DialogueState, companies: dict[str,str]):
    """作品说明：从类型化条件生成可读的当前任务投影，不解析旧回答恢复条件。"""
    from .catalog import METRICS
    from .state import financial_context_conditions
    def view(c):
        return dict(公司=[companies.get(code,code) for code in c.codes],全库=c.all_companies,
            指标=[METRICS[m].label if m in METRICS else m for m in c.metrics],
            报表范围={'consolidated':'合并','parent':'母公司'}[c.scope],
            年份=c.time.years,报告期=[{'FY':'年报全年累计','HY':'半年累计','Q1':'第一季度累计','Q3':'前三季度累计'}[p] for p in c.time.periods],
            时间选择=c.time.mode,配对期间=c.time.pairs,单季=c.time.single_quarter,
            输出单位=c.presentation.unit,小数位=c.presentation.decimals,输出形式=c.presentation.format,
            计算=c.calculation,不查数=c.restrictions.no_query,不画图=c.restrictions.no_chart)
    return dict(当前已确认任务=[dict(目标编号=key,条件=view(c)) for key,c in financial_context_conditions(state).items()],
        当前条件=view(state.pending.conditions if state.pending else state.conditions),
        待澄清=state.pending.clarification if state.pending else [],
        说明='这些是已确认条件，不是已查到数字。只修改公司、指标、时间或展示要求时沿用其余条件；查询失败不能当作有新事实。')


class Planner:
    def __init__(self, model: AsyncModel, companies: dict[str, str]):
        self.model = model
        self.companies = companies

    async def understand(self, question: str, state: DialogueState, history: list[dict], budget: Budget, feedback=()) -> Understanding:
        from .prompts import TURN
        from .proposals import TurnPlan
        from .catalog import METRICS
        from .explicit_conditions import explicit_conditions,bind_explicit
        if feedback:budget.artifacts.setdefault('correction_feedback',[]).append(list(feedback))
        payload=dict(question=question,state=planner_state(state),today=date.today().isoformat(),
            companies=self.companies,
            metrics={id:dict(name=m.label,unit=m.unit,scopes=m.scopes,aliases=m.aliases) for id,m in METRICS.items()},
            literal_bindings=explicit_conditions(question),correction_feedback=list(feedback))
        plan=await self.model.structured(TurnPlan,TURN,payload,budget)
        plan,bound=bind_explicit(plan,question,self.companies)
        if bound:budget.artifacts.setdefault('literal_bindings',[]).append(bound)
        try:
            return plan.compile(question,self.companies)
        except ValueError as exc:
            from .state import InvalidUnderstanding
            invalid=InvalidUnderstanding(str(exc));invalid.request=plan.model_dump(mode='json')
            raise invalid from exc

