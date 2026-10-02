"""作品说明：定义v3接口合同，准确财务数值以十进制字符串传递。"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, create_model, field_validator, model_validator
from .catalog import METRICS

Code = Annotated[str, StringConstraints(pattern=r'^\d{6}$')]
Year = Annotated[int, Field(ge=2000, le=2100)]
DecimalString = Annotated[str, StringConstraints(pattern=r'^-?\d+(?:\.\d+)?$')]
Period = Literal['FY', 'HY', 'Q1', 'Q3']
GoalKind = Literal['lookup', 'compare', 'rank', 'chart', 'quote', 'cause', 'concept', 'rules', 'catalog', 'sign', 'unsupported']
TaskStatus = Literal['queued', 'running', 'cancelling', 'completed', 'cancelled', 'failed']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)


class Origin(Strict):
    kind: Literal['current', 'context', 'default']
    text: str
    turn_id: str = ''


class TimeSelection(Strict):
    mode: Literal['explicit', 'calendar_years', 'latest_common', 'latest_each', 'latest'] = 'latest'
    years: list[Year] = Field(default_factory=list)
    periods: list[Period] = Field(default_factory=lambda: ['FY'])
    single_quarter: bool = False
    quarters: list[Literal[1, 2, 3, 4]] = Field(default_factory=list, description='Requested single quarters, distinct from cumulative report periods; unsupported for execution')
    span: int | None = Field(default=None, ge=1, le=30)
    pairs: list[tuple[Year, Period]] | None = None

    @field_validator('years')
    @classmethod
    def year_range(cls, years):
        if any(isinstance(y, bool) or not 2000 <= y <= 2100 for y in years):
            raise ValueError('Years must be in 2000..2100')
        return sorted(set(years))

    @field_validator('pairs')
    @classmethod
    def pair_range(cls, pairs):
        if pairs is not None and any(not 2000<=year<=2100 for year,_ in pairs):
            raise ValueError('Pair years must be in 2000..2100')
        return sorted(set(pairs)) if pairs else pairs

    @model_validator(mode='after')
    def executable_period_selection(self):
        if not self.periods and not self.single_quarter and not self.pairs:
            raise ValueError('A cumulative query requires at least one report period or explicit period pair')
        return self


class Presentation(Strict):
    unit: Literal['元', '万元', '亿元', '%', '元/股'] | None = None
    decimals: int | None = Field(default=None, ge=0, le=12)
    format: Literal['auto', 'text', 'table', 'chart'] = 'auto'
    chart_type: Literal['line', 'bar', 'pie', 'scatter'] = 'line'
    order: Literal['asc', 'desc'] = 'desc'
    limit: int | None = Field(default=None, ge=1, le=100)
    include_inputs: bool = False


class Restrictions(Strict):
    no_query: bool = False
    no_chart: bool = False
    no_repeat: bool = False
    excluded_codes: list[str] = Field(default_factory=list)
    excluded_metrics: list[str] = Field(default_factory=list)


class Conditions(Strict):
    codes: list[Code] = Field(default_factory=list)
    all_companies: bool = False
    metrics: list[str] = Field(default_factory=list)
    scope: Literal['consolidated', 'parent'] = 'consolidated'
    time: TimeSelection = Field(default_factory=TimeSelection)
    presentation: Presentation = Field(default_factory=Presentation)
    restrictions: Restrictions = Field(default_factory=Restrictions)
    calculation: Literal['none', 'difference', 'yoy', 'relative_percent', 'percentage_points'] = 'none'
    comparison_axis: Literal['years', 'companies', 'none'] = 'none'
    origins: dict[str, Origin] = Field(default_factory=dict)


class TimePatch(Strict):
    mode: Literal['explicit', 'calendar_years', 'latest_common', 'latest_each', 'latest'] | None = None
    years: list[Year] | None = None
    periods: list[Period] | None = None
    single_quarter: bool | None = None
    quarters: list[Literal[1, 2, 3, 4]] | None = None
    span: int | None = Field(default=None, ge=1, le=30)
    pairs: list[tuple[Year, Period]] | None = None


class PresentationPatch(Strict):
    unit: Literal['元', '万元', '亿元', '%', '元/股'] | None = None
    decimals: int | None = Field(default=None, ge=0, le=12)
    format: Literal['auto', 'text', 'table', 'chart'] | None = None
    chart_type: Literal['line', 'bar', 'pie', 'scatter'] | None = None
    order: Literal['asc', 'desc'] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    include_inputs: bool | None = None


class RestrictionPatch(Strict):
    no_query: bool | None = None
    no_chart: bool | None = None
    no_repeat: bool | None = None
    excluded_codes: list[Code] | None = None
    excluded_metrics: list[MetricId] | None = None


class GoalSelection(Strict):
    """作品说明：描述本轮共享条件的明确例外，独立目标保留自己的选择，避免公司和指标扩大组合。"""
    codes: list[Code] | None = None
    metrics: list[str] | None = None
    scope: Literal['consolidated', 'parent'] | None = None
    time: TimePatch | None = None
    presentation: PresentationPatch | None = None
    calculation: Literal['none', 'difference', 'yoy', 'relative_percent', 'percentage_points'] | None = None
    comparison_axis: Literal['years', 'companies', 'none'] | None = None


class Goal(Strict):
    id: str
    kind: GoalKind
    text: str
    intent_source: str = ''
    context_goal_id: str | None = None
    context_conditions: Conditions | None = None
    selection: GoalSelection | None = None
    condition_edits: list[AtomicModification] = Field(default_factory=list)
    catalog_target: Literal['companies', 'periods', 'metrics', 'capabilities'] | None = None
    execution_ref: str | None = None
    quote_mode: Literal['literal', 'location'] | None = None
    concept_mode: Literal['definition', 'difference', 'implication'] | None = None
    clarification: list[str] = Field(default_factory=list,description='只阻塞此目标的缺条件或歧义；独立且明确的其他目标仍可执行')


class CompanyMention(Strict):
    text: str = Field(description='本轮原话中实际出现的公司名字、代码、集合或代词，逐字保留')
    kind: Literal['explicit','context','collection','ambiguous','uncovered','candidate']
    codes: list[Code] = Field(description='登记名单内匹配的代码；明确库外名称填空数组，不选择相近登记公司')


class ContextReference(Strict):
    text: str = Field(min_length=1,description='本轮逐字出现的代词或回到前一财务任务的表述')
    target: Literal['company','fact','financial_task']
    number: Literal['singular','plural'] = 'singular'


class MemoryEdit(Strict):
    field: Literal['company_context','financial_context']
    operation: Literal['clear'] = 'clear'
    text: str = Field(min_length=1,description='明确删除记忆的本轮逐字依据；与新请求的空条件不同')


class ClarificationChoices(Strict):
    field: Literal['metrics'] = 'metrics'
    family: str
    options: dict[str, MetricId]


class Request(Strict):
    version: Literal[3] = 3
    turn_id: str
    question: str
    continuity: Literal['continue','new','resume','clear'] = 'new'
    conditions: Conditions
    modifications: list[Modification] = Field(default_factory=list)
    memory_edits: list[MemoryEdit] = Field(default_factory=list)
    clarification_choices: list[ClarificationChoices] = Field(default_factory=list)
    goals: list[Goal]
    context_references: list[ContextReference] = Field(default_factory=list)
    company_mentions: list[CompanyMention] = Field(default_factory=list)
    clarification: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)

    def for_goal(self, goal: Goal) -> 'Request':
        conditions = (goal.context_conditions or self.conditions).model_copy(deep=True)
        if goal.context_conditions and self.modifications:
            from .condition_updates import apply_edits
            conditions=apply_edits(conditions,self.modifications,self.question,self.turn_id)
        if goal.selection:
            for key in goal.selection.model_fields_set:
                value=getattr(goal.selection,key)
                if value is None: continue
                if key in {'time','presentation'}:
                    from .condition_updates import merge_object,patch_values
                    value=merge_object(key,getattr(conditions,key).model_dump(),patch_values(key,value))
                setattr(conditions,key,value)
        if goal.condition_edits:
            from .condition_updates import apply_edits
            conditions=apply_edits(conditions,goal.condition_edits,self.question,self.turn_id)
        from .condition_defaults import finalize_conditions
        conditions=finalize_conditions(conditions,[goal.kind],self.turn_id)
        resolved=goal.model_copy(update={'context_conditions':None,'condition_edits':[],'selection':None})
        return self.model_copy(update={'conditions': conditions, 'goals': [resolved],'modifications':[]})


MetricId = Literal[*METRICS]


class Edit(Strict):
    operation: Literal['replace', 'add', 'remove', 'keep', 'inherit', 'clear']
    text: str = ''


class CompanyEdit(Edit):
    field: Literal['codes']
    value: list[Code]


class MetricEdit(Edit):
    field: Literal['metrics']
    value: list[MetricId]


class ScopeEdit(Edit):
    field: Literal['scope']
    operation: Literal['replace', 'inherit', 'clear']
    value: Literal['consolidated', 'parent']


class CollectionEdit(Edit):
    field: Literal['all_companies']
    operation: Literal['replace', 'inherit', 'clear']
    value: bool


class TimeEdit(Edit):
    field: Literal['time']
    operation: Literal['replace', 'inherit', 'clear']
    value: TimePatch


class PresentationEdit(Edit):
    field: Literal['presentation']
    operation: Literal['replace', 'inherit', 'clear']
    value: PresentationPatch


class RestrictionEdit(Edit):
    field: Literal['restrictions']
    operation: Literal['replace', 'inherit', 'clear']
    value: RestrictionPatch


class CalculationEdit(Edit):
    field: Literal['calculation']
    operation: Literal['replace', 'inherit', 'clear']
    value: Literal['none', 'difference', 'yoy', 'relative_percent', 'percentage_points']


class AxisEdit(Edit):
    field: Literal['comparison_axis']
    operation: Literal['replace', 'inherit', 'clear']
    value: Literal['years', 'companies', 'none']


# 作品说明：新提案逐字段修改；旧对象编辑格式保留用于读取已保存的v3消息。
LEAF_SPECS={
    'time.mode':Literal['explicit','calendar_years','latest_common','latest_each','latest'],
    'time.years':list[Year], 'time.periods':list[Period],
    'time.span':Annotated[int,Field(ge=1,le=30)]|None,
    'time.pairs':list[tuple[Year,Period]]|None,
    'time.single_quarter':bool, 'time.quarters':list[Literal[1,2,3,4]],
    'presentation.unit':Literal['元','万元','亿元','%','元/股']|None,
    'presentation.decimals':Annotated[int,Field(ge=0,le=12)]|None,
    'presentation.format':Literal['auto','text','table','chart'],
    'presentation.chart_type':Literal['line','bar','pie','scatter'],
    'presentation.order':Literal['asc','desc'],
    'presentation.limit':Annotated[int,Field(ge=1,le=100)]|None,
    'presentation.include_inputs':bool,
    'restrictions.no_query':bool,'restrictions.no_chart':bool,'restrictions.no_repeat':bool,
    'restrictions.excluded_codes':list[Code], 'restrictions.excluded_metrics':list[MetricId],
}
SET_PATHS={'codes','metrics','time.years','time.periods','time.quarters',
    'restrictions.excluded_codes','restrictions.excluded_metrics'}
LEAF_EDITS=[]
for _path,_value_type in LEAF_SPECS.items():
    _name='Edit'+''.join(word.capitalize() for word in _path.replace('.','_').split('_'))
    _operations=Literal['replace','add','remove','keep','inherit','clear'] if _path in SET_PATHS else Literal['replace','inherit','clear']
    LEAF_EDITS.append(create_model(_name,__base__=Edit,__module__=__name__,
        field=(Literal[_path],...),operation=(_operations,...),value=(_value_type,...)))
ATOMIC_TYPES=[CompanyEdit,MetricEdit,ScopeEdit,CollectionEdit,CalculationEdit,AxisEdit,*LEAF_EDITS]
AtomicModification=Annotated[Union[tuple(ATOMIC_TYPES)],Field(discriminator='field')]
ATOMIC_MODIFICATION=TypeAdapter(AtomicModification)
Modification=Annotated[Union[tuple([*ATOMIC_TYPES,TimeEdit,PresentationEdit,RestrictionEdit])],Field(discriminator='field')]
MODIFICATION = TypeAdapter(Modification)


class Understanding(Strict):
    topic: Literal['financial', 'concept', 'rules', 'catalog', 'greeting', 'other']
    continuity: Literal['continue', 'new', 'resume', 'clear']
    context_references: list[ContextReference] = Field(default_factory=list)
    company_mentions: list[CompanyMention] = Field(default_factory=list, description='先识别本轮实际公司表述并区分明确、歧义、库外，再规划目标')
    goals: list[Goal]
    modifications: list[Modification]
    memory_edits: list[MemoryEdit] = Field(default_factory=list)
    clarification: list[str] = Field(description='需要用户回答的具体问题。明确公司不在库属于unknown_companies/unsupported，不是澄清；不能把公司名字单独放这里')
    unsupported: list[str]
    unknown_companies: list[str] = Field(description='原话明确指名而登记名单未覆盖的公司名字，保留原名字，不能换公司')
    claimed_sign: Literal['positive', 'negative', 'zero', 'none'] = 'none'


class OriginalRegion(Strict):
    page: int = Field(ge=1)
    bbox: tuple[float,float,float,float]
    text: str

    @model_validator(mode='after')
    def valid_rectangle(self):
        from math import isfinite
        x0,y0,x1,y1=self.bbox
        if not all(isfinite(v) for v in self.bbox) or not (0<=x0<x1 and 0<=y0<y1):
            raise ValueError('Original PDF region must be a finite positive rectangle')
        return self


class OriginalCellProof(Strict):
    value: OriginalRegion
    row: OriginalRegion
    heading: OriginalRegion
    context: OriginalRegion
    scope_context: OriginalRegion | None = None


class HashedOriginalRegion(Strict):
    page: int = Field(ge=1)
    bbox: tuple[float,float,float,float]
    text_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def valid_rectangle(self):
        OriginalRegion(page=self.page,bbox=self.bbox,text='')
        return self


class StatementScopeProof(Strict):
    caption: OriginalRegion
    family: Literal['资产负债表','利润表','现金流量表','notes','indicators']
    value_region: HashedOriginalRegion
    label_region: HashedOriginalRegion


class Source(Strict):
    document_id: str
    document_version: str
    source_sha256: str
    source_path: str
    page: int = Field(ge=1)
    table: str
    row: str
    column: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    raw_value: str
    raw_unit: str
    raw_precision: int = Field(ge=0)
    literal: str
    extraction: Literal['ocr', 'pdf_text', 'manual']
    verification: Literal['literal_checked', 'human_checked', 'unverified']
    original_cell: OriginalCellProof | None = None
    scope_proof: StatementScopeProof | None = None


class Fact(Strict):
    version: Literal[3] = 3
    id: str
    data_version: str
    stock_code: Code
    company: str
    year: int
    period: Period
    metric: str
    scope: Literal['consolidated', 'parent']
    value: DecimalString
    unit: Literal['元', '%', '元/股']
    status: Literal['verified', 'derived', 'conflict', 'scope_unknown', 'unverified']
    source: Source | None = None
    formula: str | None = None
    inputs: list[str] = Field(default_factory=list)

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.value)


class FactReference(Strict):
    id: str
    data_version: str


class ComputedReference(Strict):
    id: str
    data_version: str
    inputs: tuple[str,str]
    calculation: Literal['difference','yoy','relative_percent','percentage_points']
    comparison_axis: Literal['years','companies']
    metric: str
    scope: Literal['consolidated','parent']
    company: str
    year: Year
    period: Period
    value: DecimalString | None
    unit: Literal['元','%','元/股','百分点']
    formula: str
    label: str
    detail: str = ''

    @property
    def decimal(self):return Decimal(self.value) if self.value is not None else None


class Execution(Strict):
    turn_id: str
    data_version: str
    resolved_periods: dict[str, list[tuple[int, Period]]]
    time_rule: str
    fact_refs: list[FactReference]
    status: Literal['completed', 'partial', 'no_data', 'clarification', 'cancelled', 'failed']
    conditions: Conditions | None = None
    goal_conditions: dict[str, Conditions] = Field(default_factory=dict)


class InterruptedRequest(Strict):
    turn_id: str
    question: str
    status: Literal['failed','cancelled']
    confirmed_fields: list[Literal['codes','metrics']] = Field(default_factory=list)
    uncertain_paths: list[str] | None = None

    @field_validator('uncertain_paths')
    @classmethod
    def known_uncertain_paths(cls, value):
        if value is not None and set(value)-{'codes','metrics','scope','all_companies','calculation','comparison_axis',*LEAF_SPECS}:
            raise ValueError('Uncertainty must address registered condition leaves')
        return sorted(set(value)) if value is not None else None


class DialogueState(Strict):
    version: Literal[3] = 3
    revision: int = 0
    topic: str = 'other'
    legacy_scope_unknown: bool = False
    conditions: Conditions = Field(default_factory=Conditions)
    active_goals: list[Goal] = Field(default_factory=list)
    goal_conditions: dict[str,Conditions] = Field(default_factory=dict)
    suspended: Conditions | None = None
    pending: Request | None = None
    recent_facts: list[FactReference] = Field(default_factory=list)
    recent_computed: list[ComputedReference] = Field(default_factory=list)
    interrupted_request: InterruptedRequest | None = None
    last_execution: Execution | None = None
    executions: list[Execution] = Field(default_factory=list)
    modifications: list[Modification] = Field(default_factory=list)


class Check(Strict):
    name: str
    status: Literal['pass', 'fail', 'unknown', 'not_applicable']
    detail: str


class Verification(Strict):
    version: Literal[3] = 3
    numeric: list[Check]
    request: list[Check]
    evidence: list[Check]
    status: Literal['pass', 'partial', 'fail']


class GoalResult(Strict):
    id: str
    kind: GoalKind
    status: Literal['completed', 'partial', 'no_data', 'unsupported', 'failed']
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str = ''


class TaskRecord(Strict):
    version: Literal[3] = 3
    task_id: str
    client_request_id: str
    session_uid: str
    question: str = ''
    status: TaskStatus
    deadline: int = Field(description='Unix epoch milliseconds')
    saved: bool = False
    result: dict | None = None
    error: str | None = None


Goal.model_rebuild()
Request.model_rebuild()
