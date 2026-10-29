"""以任务意图和稀疏条件修改编译请求，理解失败保留明确失败状态。"""
from typing import Annotated,Literal
from pydantic import Field,model_validator
from .contracts import AtomicModification,CompanyMention,ContextReference,Goal,Strict,Understanding,MemoryEdit,CatalogTarget

class IntentGoalBase(Strict):
    id: str
    text: str = ''
    source_ref: str = ''
    context_goal_id: str | None = None
    execution_ref: str | None = None


class IntentGeneralGoal(IntentGoalBase):
    kind: Literal['lookup','compare','rank','chart','cause','sign','rules','unsupported']


class IntentCatalogGoal(IntentGoalBase):
    kind: Literal['catalog']
    catalog_target: CatalogTarget


class IntentQuoteGoal(IntentGoalBase):
    kind: Literal['quote']
    quote_mode: Literal['literal','location']
    quote_granularity: Literal['location','cell','row'] | None = None


class IntentConceptGoal(IntentGoalBase):
    kind: Literal['concept']
    concept_mode: Literal['definition','difference','implication']


IntentGoal=Annotated[IntentGeneralGoal|IntentCatalogGoal|IntentQuoteGoal|IntentConceptGoal,Field(discriminator='kind')]


class IntentPlan(Strict):
    """意图负责目标含义及对话连续关系，财务参数由独立编辑类型约束。"""
    continuity: Literal['new','continue','resume','clear'] = 'new'
    memory_edits: list[MemoryEdit] = Field(default_factory=list)
    context_references: list[ContextReference] = Field(default_factory=list)
    unknown_companies: list[str] = Field(default_factory=list)
    uncertain_companies: list[str] = Field(default_factory=list)
    collection_text: str | None = None
    goals: list[IntentGoal]
    unresolved_reference: str | None = None
    claimed_sign: Literal['positive','negative','zero','none'] = 'none'

    @model_validator(mode='after')
    def has_actual_goal_or_unresolved_reference(self):
        if not self.goals and not self.unresolved_reference:raise ValueError('A turn must have a typed goal or a literal unresolved reference')
        return self

    @property
    def topic(self):
        # 话题从实际目标派生，避免另一次分类与财务目标相互矛盾。
        kinds={goal.kind for goal in self.goals}
        if kinds & {'lookup','compare','rank','chart','quote','cause','sign'}:return 'financial'
        if 'concept' in kinds:return 'concept'
        if 'rules' in kinds:return 'rules'
        if 'catalog' in kinds:return 'catalog'
        return 'financial' if self.unknown_companies else 'other'


class GoalAssignment(Strict):
    id: str
    edits: list[AtomicModification] = Field(default_factory=list)
    clarification: list[str] = Field(default_factory=list)


class ConditionPlan(Strict):
    edits: list[AtomicModification]
    assignments: list[GoalAssignment] = Field(default_factory=list)
    clarification: list[str] = Field(default_factory=list)


class TurnPlan(IntentPlan):
    """一次模型提案包含目标和条件，财务结果数值不属于提案内容。"""
    edits: list[AtomicModification] = Field(default_factory=list)
    assignments: list[GoalAssignment] = Field(default_factory=list)
    clarification: list[str] = Field(default_factory=list)

    def compile(self, question: str, companies: dict[str, str]) -> Understanding:
        raw=self.model_dump(mode='json')
        conditions=ConditionPlan.model_validate({key:raw.pop(key) for key in ('edits','assignments','clarification')})
        return compile_proposal(combine_plans(IntentPlan.model_validate(raw),conditions),question,companies)


class ProposedTurn(Strict):
    intent: IntentPlan
    conditions: ConditionPlan


class FinancialClarificationRequired(ValueError):
    """已登记的财务目标需要补条件，纠正提案时禁止重复同一不支持标签。"""
    repair_goals=True
    rejected_goal_kinds=('unsupported','rules','concept','catalog')


def financial_goal_conflict(question,kinds,financial_edits,*,unknown=False,single_quarter=False):
    """格式纠错与提案编译共用财务目标矛盾检查，不增加重做次数或替模型执行查询。"""
    import re
    from .request_bindings import explicit_financial_query
    write_boundary=bool(re.search(r'(?:删除|清空|修改|更新|写入|移除).{0,18}(?:数据库|库中|库内|数据)|(?:DELETE|DROP|UPDATE|INSERT)\s',question,re.I))
    return bool(kinds and all(kind in FinancialClarificationRequired.rejected_goal_kinds for kind in kinds)
                and financial_edits and explicit_financial_query(question) and not (unknown or single_quarter or write_boundary))


class ConceptTaskRequired(ValueError):
    """明确暂停查数的概念任务不能变成带财务冲突的查询。"""
    repair_goals=True
    rejected_goal_kinds=('lookup','compare','rank','chart','cause','sign','quote','rules','unsupported','catalog')


class RequestedGoalRequired(FinancialClarificationRequired):
    """模型格式纠错和条件编译共同保留明确请求目标，缺公司不能改变查数目的。"""
    repair_goals=True
    def __init__(self,correction):
        super().__init__('A financial query awaiting missing or conflicting conditions must retain its explicit lookup goal; asking for a company does not turn it into a cause, quote or concept task')
        self.required_goal_kinds=correction['required_goal_kinds']
        self.rejected_goal_kinds=correction.get('goal_kind_exclusions',[])
        self.minimum_goal_count=correction.get('minimum_goal_count',1)


def combine_plans(intent: IntentPlan, conditions: ConditionPlan) -> ProposedTurn:
    assigned={assignment.id for assignment in conditions.assignments}
    goal_ids={goal.id for goal in intent.goals}
    if len(assigned)!=len(conditions.assignments) or not assigned<=goal_ids:
        raise ValueError('Condition assignments must uniquely reference existing intent goals')
    if len(goal_ids)!=len(intent.goals):raise ValueError('Duplicate intent goal IDs')
    return ProposedTurn(intent=intent,conditions=conditions)


def compile_proposal(proposal: ProposedTurn, question: str, companies: dict[str,str]) -> Understanding:
    intent=proposal.intent;conditions=proposal.conditions;mentions=[]
    from .request_bindings import explicit_collection_span,unregistered_company_literals,memory_clear_bindings
    clears=memory_clear_bindings(question,companies)
    memory_edits=list(intent.memory_edits)
    # 明确删除记忆的保守保护与任务解析分开；新措辞仍可由带原话依据的类型化操作表达。
    for field,spans in clears.items():
        if spans and not any(edit.field==field for edit in memory_edits):
            memory_edits.append(MemoryEdit(field=field,text=sorted(spans)[0]))
    for edit in memory_edits:
        if not edit.text or edit.text not in question:
            if clears[edit.field]:edit.text=sorted(clears[edit.field])[0]
            else:raise ValueError('A memory edit must identify its literal current-input instruction')
    if intent.continuity=='clear' and not any(edit.field=='financial_context' for edit in memory_edits):
        raise ValueError('Clearing financial context requires an explicit memory edit')
    for reference in intent.context_references:
        if reference.text not in question:raise ValueError('A context reference must be a literal current input span')
    company_clear=any(edit.field=='company_context' for edit in memory_edits)
    if intent.context_references and intent.continuity=='new' and not (company_clear and all(r.target=='company' for r in intent.context_references)):
        raise ValueError('An independent new question cannot borrow context references')
    unknown=list(intent.unknown_companies)
    if intent.topic=='financial' and not unknown:unknown=unregistered_company_literals(question,companies)
    from .company_identity import company_directory,normalized_text
    directory=company_directory(companies)
    for kind,names in [('uncovered',unknown),('candidate',[name for name in intent.uncertain_companies if name not in unknown])]:
        for name in names:
            if not name or name not in question:raise ValueError('An unresolved company name must be a literal current-input span')
            if len(directory.aliases.get(normalized_text(name),()))==1:
                raise ValueError('An unresolved company contradicts an explicit registered identity')
            mentions.append(CompanyMention(text=name,kind=kind,codes=[]))
    for match in directory.matches(question):
        if len(match.codes)==1 and not any(match.text in item.text and item.text!=match.text for item in mentions):
            mentions.append(CompanyMention(text=match.text,kind='explicit',codes=list(match.codes)))
    edits=[*conditions.edits,*(edit for assignment in conditions.assignments for edit in assignment.edits)]
    collection=intent.collection_text or next((edit.text for edit in edits
        if edit.field=='all_companies' and edit.operation!='clear' and edit.value is True),None)
    if collection is not None:
        if not collection or collection not in question:
            raise ValueError('Collection provenance must occur literally in this input')
        mentions.append(CompanyMention(text=collection,kind='collection',codes=[]))
    assigned={assignment.id:assignment for assignment in conditions.assignments}
    for edit in edits:
        if not edit.text or edit.text not in question:raise ValueError('Every edit must identify its literal current-input span')
    goals=[]
    for goal in intent.goals:
        raw=goal.model_dump(mode='json')
        span=raw.pop('source_ref') or question
        if span not in question:raise ValueError('A goal must refer to a literal current-input source')
        raw['intent_source']=span
        raw['text']=raw['text'] or span
        if assignment:=assigned.get(goal.id):
            raw['condition_edits']=assignment.edits
            raw['clarification']=assignment.clarification
        goals.append(Goal.model_validate(raw))
    # 边界回复来自固定能力说明，避免模型自由回答无关问题或重述整段用户输入。
    unsupported=['这项请求不在当前财报助手的支持范围。我可以查询库内公司的财务数字、比较指标和回查原件，暂不提供实时行情、单季数据或通用生活与创作服务。'
                 '你可以先问：“目前收录了哪些公司和报告期？”再查询相应财务指标。'] if any(goal.kind=='unsupported' for goal in goals) else []
    # 工具能力只有参数化只读查询；用户写库要求与合法查询分别记录，不允许模型把写库当可执行目标。
    import re
    financial_edits=any(edit.field in {'metrics','codes','time.years','time.pairs'} and edit.value for edit in edits)
    single_quarter=any(edit.field=='time.single_quarter' and edit.value is True for edit in edits)
    write_boundary=bool(re.search(r'(?:删除|清空|修改|更新|写入|移除).{0,18}(?:数据库|库中|库内|数据)|(?:DELETE|DROP|UPDATE|INSERT)\s',question,re.I))
    # 财务实体并不能证明用户要求查询历史事实；预测、能力边界和概念任务保持模型的类型化选择。
    # 缺公司或指标由查询目标编译后的条件完整性检查处理，格式纠错不再擅自强制lookup。
    if re.search(r'(?:删除|清空|修改|更新|写入|移除).{0,18}(?:数据库|库中|库内|数据)|(?:DELETE|DROP|UPDATE|INSERT)\s',question,re.I):
        unsupported.append('本系统只能只读查询，不能删除、修改或写入数据库；独立的合法查询仍按原件核验。')
    clarification=list(dict.fromkeys(conditions.clarification))
    if intent.unresolved_reference:
        if intent.unresolved_reference not in question:raise ValueError('An unresolved reference must be a literal current-input span')
        clarification.append('请明确“'+intent.unresolved_reference+'”指什么或希望完成什么任务。')
    deltas=conditions.edits
    if intent.topic=='other':deltas=[]
    return Understanding(topic=intent.topic,continuity=intent.continuity,goals=goals,modifications=deltas,
        memory_edits=memory_edits,
        context_references=intent.context_references,company_mentions=mentions,clarification=clarification,
        unsupported=unsupported,unknown_companies=unknown,claimed_sign=intent.claimed_sign)
