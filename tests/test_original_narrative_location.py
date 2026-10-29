"""来源位置可借助OCR，但摘录必须使用完整原句且拒绝错页、改数和歧义。"""
import pytest
from src.utils.original_narratives import original_sentence_between_anchors

ORIGINAL='报告期内，公司实现经营活动现金流量净额-321.45万元，主要由于采购材料及服务支出增加，同期收回货款有所减少所致。'
OCR=ORIGINAL.replace('材料及服务','材料与服务')


def test_relocation_returns_original_characters_including_line_breaks():
    original=ORIGINAL.replace('采购材料','采购\n材料')
    assert original_sentence_between_anchors('上一段已结束。\n'+original,OCR)==original
    assert '材料及服务' in original_sentence_between_anchors(original,OCR)


@pytest.mark.parametrize('original,candidate',[
    (ORIGINAL+'\n'+ORIGINAL,OCR),
    (ORIGINAL.replace('-321.45','321.45'),OCR),
    (ORIGINAL.replace('321.45','321.46'),OCR),
    (ORIGINAL.replace('采购材料','采购结束。购买材料'),OCR),
    ('并非'+ORIGINAL,OCR),
    ('完全无关的另一页内容。',OCR),
    (ORIGINAL,OCR.replace('公司实现','企业取得')),
    (ORIGINAL,OCR.replace('减少所致','增加所致')),
])
def test_relocation_rejects_ambiguous_or_changed_anchor_or_quantity(original,candidate):
    assert original_sentence_between_anchors(original,candidate) is None


def test_location_still_requires_original_metric_and_cause_binding(monkeypatch,tmp_path):
    import fitz
    from src.agent.v3 import evidence
    path=tmp_path/'source.pdf';doc=fitz.open();doc.new_page();doc.save(path);doc.close()
    monkeypatch.setattr(evidence,'source_pdf',lambda _:path)
    monkeypatch.setattr(fitz.Page,'get_text',lambda _:ORIGINAL)
    snippet=dict(id='s',source_path='registered.pdf',document_version='verified-hash',page=1,text=OCR,target_metric='operating_cash_flow')
    got=evidence.checked_narrative_literal(snippet)
    assert got['text']==ORIGINAL and got['location_method']=='unique_original_sentence_anchors'
    assert got['literal_verification']=='matched_original_pdf_page'
    assert evidence.checked_narrative_literal({**snippet,'target_metric':'operating_revenue'}) is None
    assert evidence.checked_narrative_literal({**snippet,'page':2}) is None
