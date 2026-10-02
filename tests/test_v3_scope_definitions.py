from src.agent.v3.catalog import metric_definition


def test_parent_profit_and_margin_definitions_keep_the_standalone_scope():
    assert '少数股东' in metric_definition('net_profit','consolidated')
    parent=metric_definition('net_profit','parent')
    assert '单体' in parent and '不包含子公司' in parent and '含归母与少数股东' not in parent
    assert '母公司净利润 / 母公司营业收入' in metric_definition('net_margin','parent')
