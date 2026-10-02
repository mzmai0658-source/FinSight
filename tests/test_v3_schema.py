from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.generate_v3_contracts import generate
from src.api.schemas import InternalChatRequest


def test_generated_contracts_are_current():
    root=Path(__file__).resolve().parents[1]
    schema, ts=generate()
    assert (root/'shared/finsight-v3.schema.json').read_text(encoding='utf-8') == schema
    assert (root/'frontend/src/types/finsight-v3.ts').read_text(encoding='utf-8') == ts


def test_internal_question_normalizes_before_length_validation():
    assert InternalChatRequest(question=' \t问题\n ').question == '问题'
    assert len(InternalChatRequest(question='😀'*2000).question)==2000
    for text in [' \t\r\n', '\u0085\u00a0', '问'*2001]:
        with pytest.raises(ValidationError): InternalChatRequest(question=text)
