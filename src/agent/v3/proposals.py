"""作品说明：以任务意图和稀疏条件修改编译请求，理解失败保留明确失败状态。"""
from typing import Annotated,Literal
from pydantic import Field,model_validator
from .contracts import AtomicModification,CompanyMention,ContextReference,Goal,Strict,Understanding,MemoryEdit

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
    catalog_target: Literal['companies','periods','metrics','capabilities']


class IntentQuoteGoal(IntentGoalBase):
    kind: Literal['quote']
    quote_mode: Literal['literal','location']


class IntentConceptGoal(IntentGoalBase):
    kind: Literal['concept']
    concept_mode: Literal['definition','difference','implication']


IntentGoal=Annotated[IntentGeneralGoal|IntentCatalogGoal|IntentQuoteGoal|IntentConceptGoal,Field(discriminator='kind')]


class IntentPlan(Strict):
    """作品说明：意图负责目标含义及对话连续关系，财务参数由独立编辑类型约束。"""
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
        # 作品说明：话题从实际目标派生，避免另一次分类与财务目标相互矛盾。
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
    """作品说明：一次模型提案包含目标和条件，财务结果数值不属于提案内容。"""
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


def combine_plans(intent: IntentPlan, conditions: ConditionPlan) -> ProposedTurn:
    assigned={assignment.id for assignment in conditions.assignments}
    goal_ids={goal.id for goal in intent.goals}
    if len(assigned)!=len(conditions.assignments) or not assigned<=goal_ids:
        raise ValueError('Condition assignments must uniquely reference existing intent goals')
    if len(goal_ids)!=len(intent.goals):raise ValueError('Duplicate intent goal IDs')
    return ProposedTurn(intent=intent,conditions=conditions)


def compile_proposal(proposal: ProposedTurn, question: str, companies: dict[str,str]) -> Understanding:
    intent=proposal.intent;conditions=proposal.conditions;mentions=[]
    from .request_bindings import memory_clear_bindings,explicit_collection_span,unregistered_company_literals
    clears=memory_clear_bindings(question,companies)
    # 作品说明：明确删除记忆是用户操作，由程序登记，不能依赖模型选择是否执行。
    memory_edits=list(intent.memory_edits)
    for field,spans in clears.items():
        if spans and not any(edit.field==field for edit in memory_edits):
            memory_edits.append(MemoryEdit(field=field,operation='clear',text=sorted(spans)[0]))
    if intent.continuity=='clear' and not clears['financial_context']:
        raise ValueError('Clearing the whole financial context requires an explicit current-input instruction')
    for reference in intent.context_references:
        if reference.text not in question:raise ValueError('A context reference must be a literal current input span')
    for edit in intent.memory_edits:
        if not clears[edit.field]:
            raise ValueError('A memory clear must be supported by an explicit instruction to delete that context')
    # 作品说明：清空记忆的依据由程序定位；模型生成的引文措辞不成为授权来源。
    memory_edits=[MemoryEdit(field=edit.field,operation='clear',text=sorted(clears[edit.field])[0]) for edit in memory_edits]
    if intent.context_references and intent.continuity=='new' and not (clears['company_context'] and all(r.target=='company' for r in intent.context_references)):
        raise ValueError('An independent new question cannot borrow context references')
    unknown=list(intent.unknown_companies)
    if intent.topic=='financial' and not unknown:unknown=unregistered_company_literals(question,companies)
    for kind,names in [('uncovered',unknown),('candidate',intent.uncertain_companies)]:
        for name in names:
            if not name or name not in question:raise ValueError('An unresolved company name must be a literal current-input span')
            mentions.append(CompanyMention(text=name,kind=kind,codes=[]))
    for code,name in companies.items():
        for span in (code,name):
            if span in question and not any(span in unresolved.text and unresolved.text!=span for unresolved in mentions):
                mentions.append(CompanyMention(text=span,kind='explicit',codes=[code]))
    collection=intent.collection_text or explicit_collection_span(question)
    if collection is not None:
        if not collection or collection not in question:
            raise ValueError('Collection provenance must occur literally in this input')
        mentions.append(CompanyMention(text=collection,kind='collection',codes=[]))
    assigned={assignment.id:assignment for assignment in conditions.assignments}
    edits=[*conditions.edits,*(edit for assignment in conditions.assignments for edit in assignment.edits)]
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
    unsupported=['当前能力无法执行该请求：'+goal.text for goal in goals if goal.kind=='unsupported']
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
