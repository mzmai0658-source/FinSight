from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest
from src.agent.v3.model import sampling_schema
from src.agent.v3.proposals import IntentPlan,ConditionPlan,combine_plans


def test_topic_is_derived_from_actual_goals_and_cannot_contradict_them():
    grammar=sampling_schema(IntentPlan.model_json_schema())
    assert 'topic' not in grammar['properties'] and 'clarification' not in grammar['properties']
    validator=Draft202012Validator(grammar)
    for kind,topic,extra in [('lookup','financial',{}),('rules','rules',{}),('concept','concept',{'concept_mode':'implication'}),('catalog','catalog',{'catalog_target':'companies'}),('unsupported','other',{})]:
        value=dict(goals=[dict(id='g',kind=kind,text='当前目标',**extra)])
        assert validator.is_valid(value)
        assert IntentPlan.model_validate(value).topic==topic
    with pytest.raises(ValidationError):IntentPlan(topic='other',goals=[dict(id='g',kind='rules',text='规则')])
    with pytest.raises(ValidationError):IntentPlan(goals=[dict(id='g',kind='lookup',text='查数',clarification=['请明确公司'])])


def test_intent_cannot_assign_conditions_or_conditions_invent_goal_ids():
    with pytest.raises(ValidationError):IntentPlan(goals=[dict(id='lookup',kind='lookup',text='数字',selection=dict(codes=['600085']))])
    intent=IntentPlan(goals=[dict(id='lookup',kind='lookup',text='数字')])
    with pytest.raises(ValueError):combine_plans(intent,ConditionPlan(edits=[],assignments=[dict(id='invented',edits=[])]))


