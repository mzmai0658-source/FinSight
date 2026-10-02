from jsonschema import Draft202012Validator
from src.agent.v3.model import sampling_schema
from src.agent.v3.model_wire import LABELS,encode_schema,translate_data
from src.agent.v3.proposals import IntentPlan
from src.agent.v3.planner import SemanticReview


def test_context_sampling_and_decoding_have_one_owner_of_the_relationship():
    import pytest
    from src.agent.v3.model_wire import intent_context_schema,decode_intent_context
    schema=encode_schema(intent_context_schema(sampling_schema(IntentPlan.model_json_schema())))
    goals=[dict(id='q',kind='lookup',source_ref='它的营收')]
    value=translate_data(dict(context_selection=dict(goals=goals,continuity='new',context_references=[])))
    validator=Draft202012Validator(schema)
    assert validator.is_valid(value)
    conflict={**value,'context_selection':translate_data(dict(goals=goals,continuity='new',context_references=[dict(text='它',target='company')]))}
    assert not validator.is_valid(conflict)
    with pytest.raises(ValueError,match='cannot borrow'):
        decode_intent_context(translate_data(conflict,decode=True))
    continued={**value,'context_selection':translate_data(dict(goals=goals,continuity='continue',context_references=[dict(text='它',target='company')]))}
    assert validator.is_valid(continued)
    assert IntentPlan.model_validate(decode_intent_context(translate_data(continued,decode=True))).continuity=='continue'
    with pytest.raises(ValueError,match='Duplicate owners'):
        decode_intent_context({**translate_data(value,decode=True),'continuity':'new'})


def test_model_labels_are_bijective_and_do_not_translate_financial_or_source_values():
    for values in LABELS.values():assert len(set(values.values()))==len(values)
    original=dict(continuity='continue',kind='rules',source_ref='rules',text='lookup',
        nested=[dict(kind='current'),dict(kind='concept',concept_mode='implication')])
    wire=translate_data(original)
    assert wire['kind']=='解释本系统的实际执行记录或规则'
    assert wire['text']=='lookup' and wire['source_ref']=='rules' and wire['nested'][0]['kind']=='current'
    assert translate_data(wire,decode=True)==original


def test_readable_sampling_enums_validate_the_same_intent_and_audit_contract():
    intent=dict(continuity='continue',goals=[dict(id='t',kind='rules',source_ref='原话')])
    schema=encode_schema(sampling_schema(IntentPlan.model_json_schema()))
    wire=translate_data(intent)
    assert Draft202012Validator(schema).is_valid(wire)
    assert not Draft202012Validator(schema).is_valid(intent)
    assert IntentPlan.model_validate(translate_data(wire,decode=True)).goals[0].kind=='rules'
    audit=dict(goal_requirements=[dict(kind='rules',goal_ids=['t'],representation='represented')],satisfied=True,clarification=[],planner_defects=[],
        companies_correct=True,metrics_and_scope_correct=True,time_correct=True,goals_correct=True,constraints_correct=True,presentation_correct=True)
    assert Draft202012Validator(encode_schema(sampling_schema(SemanticReview.model_json_schema()))).is_valid(translate_data(audit))
