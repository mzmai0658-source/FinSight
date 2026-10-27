"""显式选择的OCR宽松资料库，不改变正式发布指针和完整核验规则。"""
from __future__ import annotations

from sqlalchemy import select

from src.agent.v3.repository import CanonicalRepository, releases, pointer, json_payload


class TrialRepository(CanonicalRepository):
    pointer_name = 'ocr_trial'
    release_status = 'experimental'
    fact_statuses = ('verified', 'derived', 'unverified')
    numeric_source_policy = 'ocr_trial'

    def __init__(self, engine, version=None):
        super().__init__(engine, version)
        if self.manifest.get('review_mode')!='relaxed_ocr' or self.manifest.get('dataset_profile',{}).get('id')!='ocr-trial-v1':
            raise ValueError('Trial repository requires an explicitly labelled experimental inventory')


def trial_source_ok(fact):
    """只检查候选自洽与原件身份，明确不替代原件行列、表范围核验。"""
    from src.agent.v3.evidence import source_pdf
    from src.utils.financial_numbers import cell_decimal, normalize_cell
    source=fact.source
    if source is None or source.verification!='unverified' or source.document_version!=source.source_sha256:
        return False
    try:
        if source_pdf(source) is None:return False
        raw=cell_decimal(source.raw_value)
        return (raw is not None and source.raw_precision==max(0,-raw.as_tuple().exponent)
                and normalize_cell(source.raw_value,source.raw_unit,fact.unit)==fact.decimal)
    except (ValueError,OSError,ArithmeticError):
        return False


def publish_trial(engine, version, acceptance, *, validate_snapshot=None):
    """单独试验指针只接受清单与向量完整性，不声称来源审核已经完成。"""
    if any(acceptance.get(key) is not True for key in ('inventory_checked','index_verified')):
        raise ValueError('Trial inventory/index acceptance is incomplete')
    with engine.begin() as conn:
        row=conn.execute(select(releases).where(releases.c.version==version).with_for_update()).mappings().one()
        manifest=json_payload(row['manifest'])
        if manifest.get('review_mode')!='relaxed_ocr' or manifest.get('dataset_profile',{}).get('id')!='ocr-trial-v1':
            raise ValueError('Trial publication requires an explicit policy and inventory')
        from src.etl.release_profiles import release_profile
        release_profile(manifest)
        if row['status'] not in {'staged','experimental'}:
            raise ValueError('Only an experimental candidate may be published to the trial pointer')
        if validate_snapshot is not None:
            validate_snapshot(conn)
        if row['status']=='staged':
            manifest={**manifest,'acceptance':{**acceptance,'facts_verified':False,'source_audit_complete':False}}
            conn.execute(releases.update().where(releases.c.version==version).values(status='experimental',manifest=manifest))
        conn.execute(pointer.delete().where(pointer.c.name=='ocr_trial'))
        conn.execute(pointer.insert().values(name='ocr_trial',version=version))


def configured_trial_agent(engine):
    """主项目可明确选择试验资料；未选择时仍保持严格来源策略。"""
    import os
    from src.etl.submission_upgrade import DATABASE, PROFILE
    policy=os.getenv('FINSIGHT_DATA_PROFILE','').strip()
    legacy_lab=(not policy and os.getenv('FINSIGHT_UPGRADE_PROFILE')==PROFILE
                and engine.url.database==DATABASE)
    if os.getenv('FINSIGHT_OCR_TRIAL')!='true' or not (policy=='ocr-trial-v1' or legacy_lab):
        raise ValueError('OCR trial requires an explicit ocr-trial-v1 data profile and enabled trial policy')
    from src.agent.v3.published_agent import PublishedAgent
    return PublishedAgent(engine,repository_factory=TrialRepository)
