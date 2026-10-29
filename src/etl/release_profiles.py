"""按明确的数据集覆盖验收发布，合成演示与真实原件共用核验规则。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseProfile:
    id: str
    kind: str
    reports: int
    companies: int
    codes: tuple[str, ...] = ()

    def public_metadata(self):
        if self.id == 'ocr-trial-v1':
            return dict(id=self.id,kind=self.kind,reports=self.reports,companies=self.companies,
                        label='真实财报OCR试验数据（未完整核验）',source_audit_complete=False)
        return dict(id=self.id, kind=self.kind, reports=self.reports, companies=self.companies,
                    label='虚构演示数据' if self.kind == 'synthetic' else '真实财报验证数据')


REAL = ReleaseProfile('real-117-v1', 'real', 117, 10)
DEMO = ReleaseProfile('synthetic-demo-v3', 'synthetic', 15, 5,
                      tuple(f'99000{i}' for i in range(1, 6)))
PROFILES = {p.id: p for p in (REAL, DEMO)}


def release_profile(manifest):
    # 已发布历史清单没有配置标识时仍按严格的真实范围检查。
    value = manifest.get('dataset_profile') or {'id': REAL.id}
    identity = value if isinstance(value, str) else value['id']
    if identity in {'user-financial-v1', 'ocr-trial-v1'}:
        if not isinstance(value, dict) or value.get('kind') != 'real':
            raise ValueError('Uploaded financial reports require an explicit real-data inventory')
        inventory = value.get('inventory')
        if not isinstance(inventory, list) or not inventory:
            raise ValueError('Uploaded financial report inventory is missing')
        keys = [(r['stock_code'], r['year'], r['period']) for r in inventory]
        if len(set(keys)) != len(keys) or any(not r.get('document_version') for r in inventory):
            raise ValueError('Uploaded financial report inventory is ambiguous')
        reports, companies = len(inventory), len({r['stock_code'] for r in inventory})
        if value.get('reports') != reports or value.get('companies') != companies:
            raise ValueError('Uploaded profile counts differ from its original inventory')
        return ReleaseProfile(identity, 'real', reports, companies)
    if identity not in PROFILES:
        raise ValueError('Unknown release profile')
    profile = PROFILES[identity]
    if isinstance(value, dict):
        for field in ('kind', 'reports', 'companies'):
            if field in value and value[field] != getattr(profile, field):
                raise ValueError('Release profile coverage changed')
    return profile


def check_report_coverage(manifest, reports):
    profile = release_profile(manifest)
    identities = {(r['stock_code'], r['year'], r['period']) for r in reports}
    codes = {r['stock_code'] for r in reports}
    if len(reports) != len(identities) or len(identities) != profile.reports or len(codes) != profile.companies:
        raise ValueError('Report coverage does not match the release profile')
    if manifest.get('reports') != profile.reports:
        raise ValueError('Manifest report count mismatch')
    if profile.id in {'user-financial-v1', 'ocr-trial-v1'}:
        actual = {(r['stock_code'], r['year'], r['period'], r['document_version']) for r in reports}
        expected = {(r['stock_code'], r['year'], r['period'], r['document_version'])
                    for r in manifest['dataset_profile']['inventory']}
        if actual != expected:
            raise ValueError('Uploaded financial report identities differ from the frozen inventory')
    if profile.codes and identities != {(c, y, 'FY') for c in profile.codes for y in (2022, 2023, 2024)}:
        raise ValueError('Synthetic report identities changed')
    return profile
