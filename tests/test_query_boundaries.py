from src.agent.facts import extract_report_periods, extract_report_years, metadata_matches, metric_mentions, request_scope, requested_metric_fields


from src.agent.domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP


from src.agent.verifier import verify_numbers


def test_report_period_nicknames_do_not_split_one_period_into_two():
    assert extract_report_periods("22年一季报净利润") == ["Q1"]
    assert extract_report_periods("22年半年净利润") == ["HY"]
    assert extract_report_periods("22年半年报净利润") == ["HY"]
    assert extract_report_periods("上半年度净利润") == ["HY"]


def test_short_metric_names_and_a_trend_request_are_understood():
    from src.agent.entity_linker import link_entities
    assert link_entities("净利多少").metric_ids() == ["net_profit"]
    assert link_entities("毛利多少").metric_ids() == ["gross_profit_margin"]
    assert link_entities("扣非多少").metric_ids() == []
    assert link_entities("扣非多少").ambiguous_metrics
    assert link_entities("营业额多少").metric_ids() == ["total_operating_revenue"]
    assert link_entities("利润趋势").drawing is True


def test_colloquial_two_digit_years_are_normalized():
    assert extract_report_years("凯莱英23和24年的主要财务指标") == [2023, 2024]
    assert extract_report_years("万邦德22到24这几年营收咋走") == [2022, 2023, 2024]
    assert extract_report_years("23/24年收入对比") == [2023, 2024]
    assert extract_report_years("盘龙药业去年前三季度", current_year=2026) == [2025]
    assert extract_report_years("近三年年报", current_year=2026) == [2023, 2024, 2025]
    assert extract_report_years("把22，23，24和三年的利润画个图") == [2022, 2023, 2024]
    assert extract_report_years("22、23、24年净利润") == [2022, 2023, 2024]
    assert extract_report_years("22 23 24年利润") == [2022, 2023, 2024]
    assert extract_report_years("二〇二二到二〇二四年利润") == [2022, 2023, 2024]
    assert extract_report_years("二〇二四年营收") == [2024]


def test_colloquial_metrics_and_main_numbers_are_structured():
    assert {item[2] for item in metric_mentions("盘龙药业去年赚了多少钱")} == {"net_profit"}
    assert {item[2] for item in metric_mentions("24年收入和负债有多少")} == {
        "total_operating_revenue", "liability_total_liabilities"
    }
    assert requested_metric_fields("凯莱英24年主要数字都看一下") == {
        "total_operating_revenue", "net_profit", "gross_profit_margin", "roe"
    }


def test_report_period_is_exact_and_mixed_periods_keep_year_pairing():
    q3 = request_scope("凯莱英23、24年前三季度表现咋样")
    assert q3["year_periods"] == [(2023, "Q3"), (2024, "Q3")]
    assert metadata_matches(
        {"stock_code": "002821", "report_year": 2024, "report_period": "Q3"}, q3
    )
    assert not metadata_matches(
        {"stock_code": "002821", "report_year": 2024, "report_period": "HY"}, q3
    )

    mixed = request_scope("凯莱英24年上半年跟23年全年相比")
    assert set(mixed["year_periods"]) == {(2024, "HY"), (2023, "FY")}
    assert not metadata_matches(
        {"stock_code": "002821", "report_year": 2023, "report_period": "HY"}, mixed
    )

