"""有限示范必须与生产合同一致，不能再增加题目专用规则。"""
import json

from jsonschema import Draft202012Validator

from src.agent.v3.contracts import Conditions
from src.agent.v3.model_wire import encode_schema, translate_data
from src.agent.v3.proposals import TurnPlan
from src.agent.v3.turn_transport import turn_schema, decode_turn


def test_prompt_is_one_bounded_version_with_valid_calculation_and_scope_examples():
    from src.agent.v3.prompts import TURN
    examples=[json.loads(line) for line in TURN.splitlines() if line.startswith('{"question"')]
    assert 1 <= len(examples) <= 6
    assert any('calculation_inputs' in goal.get('edits',{}) for example in examples for goal in example['output']['goals'])
    assert any('session' in json.dumps(example['output']) for example in examples)
    for example in examples:
        schema=encode_schema(turn_schema(TurnPlan.model_json_schema(), dict(question=example['question'],
            companies={'990101':'甲公司','990102':'乙公司','990103':'丙公司'},state=example['context'])))
        Draft202012Validator(schema).validate(example['output'])
        TurnPlan.model_validate(decode_turn(translate_data(example['output'],decode=True),example['question']))


def test_operand_contract_has_no_answer_or_source_input_and_keeps_legacy_state_readable():
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        Conditions(calculation_inputs=[dict(current={'year':2024,'value':'999'},base={'year':2023})])
    old=Conditions.model_validate(dict(codes=['990101'],metrics=['operating_revenue'],calculation='yoy'))
    assert old.calculation_inputs == [] and old.presentation.metric_units == {}


def test_prompt_example_financial_identities_match_their_predeclared_tasks():
    from src.agent.v3.prompts import TURN
    examples=[json.loads(line) for line in TURN.splitlines() if line.startswith('{"question"')]
    expected=[{'operating_revenue'},set(),{'operating_revenue','net_profit','attributable_net_profit'},
        {'operating_revenue'},set(),{'operating_revenue'}]
    for example,metrics in zip(examples,expected):
        edits=example['output'].get('edits',{})
        actual={metric for item in edits.get('metrics',[]) for metric in item['value']}
        actual.update(metric for assignment in example['output']['goals']
            for item in assignment['edits'].get('metrics',[]) for metric in item['value'])
        assert actual==metrics


def test_compact_model_contract_contains_definitions_for_every_remaining_reference():
    from src.agent.v3.turn_transport import turn_guide
    schema=turn_schema(TurnPlan.model_json_schema(),dict(question='计算已选期间的变化',companies={},state={}))
    guide=turn_guide(schema)
    def refs(value):
        if isinstance(value,dict):
            return ({value['$ref'].rsplit('/',1)[-1]} if '$ref' in value else set()).union(*(refs(v) for v in value.values()))
        if isinstance(value,list):return set().union(*(refs(v) for v in value))
        return set()
    assert refs(guide)<=set(guide.get('$defs',{}))
