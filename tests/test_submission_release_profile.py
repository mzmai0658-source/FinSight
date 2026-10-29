"""上传发布必须以显式原件清单核对覆盖，不采用旧117份常量。"""
import pytest


def test_dynamic_profile_checks_exact_identity_inventory():
    from src.etl.release_profiles import check_report_coverage
    report = dict(stock_code='000789', year=2025, period='Q3', document_version='a' * 64)
    profile = dict(id='user-financial-v1', kind='real', reports=1, companies=1,
                   inventory=[{key: report[key] for key in ('stock_code', 'year', 'period', 'document_version')}])
    manifest = dict(dataset_profile=profile, reports=1)
    assert check_report_coverage(manifest, [report]).reports == 1
    with pytest.raises(ValueError):
        check_report_coverage(manifest, [{**report, 'year': 2024}])
    with pytest.raises(ValueError):
        check_report_coverage(manifest, [{**report, 'document_version': 'b' * 64}])


def test_dynamic_profile_cannot_claim_real_data_without_inventory():
    from src.etl.release_profiles import release_profile
    with pytest.raises(ValueError):
        release_profile(dict(dataset_profile=dict(id='user-financial-v1', kind='real', reports=1, companies=1)))
