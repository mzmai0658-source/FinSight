"""真实资料的清单校验、注册和v3发布共用入口，不导入旧数据库快照。"""
from __future__ import annotations
import hashlib,json,os,re,stat,subprocess,sys,zipfile
from pathlib import Path,PurePosixPath
from sqlalchemy import create_engine,select,text

ROOT=Path(__file__).resolve().parents[2]

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def safe_member(name):
    path=PurePosixPath(name)
    if not name or '\\' in name or path.is_absolute() or any(p in {'..','.'} or ':' in p or p.rstrip(' .')!=p for p in path.parts):
        raise ValueError('Unsafe archive path')
    if path.as_posix()!=name or any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p) for p in path.parts):
        raise ValueError('Ambiguous archive path')
    return path

def check_archive(path,expected=None):
    if expected and digest(path)!=expected:raise ValueError('Archive SHA-256 mismatch')
    with zipfile.ZipFile(path) as archive:
        names=set();total=0
        for item in archive.infolist():
            safe_member(item.filename)
            key=item.filename.casefold()
            if key in names or item.is_dir() or stat.S_ISLNK(item.external_attr>>16) or item.flag_bits&1:
                raise ValueError('Duplicate, encrypted, or non-file archive member')
            names.add(key);total+=item.file_size
            if total>2_000_000_000:raise ValueError('Archive expansion exceeds the dataset limit')
        manifest=json.loads(archive.read('PACKAGE_MANIFEST.json'))
        if manifest.get('schema')!=1 or manifest.get('dataset')!='real-117-v1':raise ValueError('Unsupported real dataset package')
        expected_names={'package_manifest.json'}|{row['path'].casefold() for row in manifest['files']}
        if len(manifest['files'])+1!=len(names) or names!=expected_names:raise ValueError('Archive inventory mismatch')
        for row in manifest['files']:
            safe_member(row['path'])
            info=archive.getinfo(row['path'])
            if info.file_size!=row['bytes']:raise ValueError('Packaged file size mismatch')
            with archive.open(row['path']) as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=row['sha256']:raise ValueError('Packaged file SHA-256 mismatch')
        bad=archive.testzip()
        if bad:raise ValueError('Archive CRC failed')
    return manifest

def import_packages(package_set,include_ocr=True):
    package_set=Path(package_set).resolve();spec=json.loads(package_set.read_text(encoding='utf-8'))
    if spec.get('dataset')!='real-117-v1' or spec.get('schema')!=1:raise ValueError('Unsupported package set')
    checked=[]
    for row in spec['packages']:
        if row['kind']=='ocr' and not include_ocr:continue
        name=safe_member(row['file'])
        if len(name.parts)!=1:raise ValueError('Package archive must be next to PACKAGE_SET.json')
        archive=package_set.parent/row['file'];manifest=check_archive(archive,row['sha256'])
        if manifest['data_version']!=spec['data_version']:raise ValueError('Packages bind different data versions')
        if manifest['kind']!=row['kind']:raise ValueError('Package kind mismatch')
        checked.append((archive,manifest))
    # 所有附件先完整校验，再写文件；同一版本重复导入只接受相同字节。
    planned={}
    for archive,manifest in checked:
        for row in manifest['files']:
            name=row['path']
            if not name.startswith(('data_root/','data/reference/v3/','data/runtime/real-package/','docs/data-notices/')):
                raise ValueError('Package tries to replace code or configuration')
            if name in planned and planned[name]!=row['sha256']:raise ValueError('Package file collision')
            planned[name]=row['sha256'];target=(ROOT/name).resolve()
            if not target.is_relative_to(ROOT):raise ValueError('Package target escapes this installation')
            if target.exists() and digest(target)!=row['sha256']:raise ValueError('Existing dataset bytes differ; use an empty installation')
    for archive,manifest in checked:
        with zipfile.ZipFile(archive) as source:
            for row in manifest['files']:
                target=ROOT/row['path']
                if target.exists():continue
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_name(target.name+'.installing')
                with source.open(row['path']) as incoming,temporary.open('wb') as outgoing:
                    import shutil
                    shutil.copyfileobj(incoming,outgoing)
                if digest(temporary)!=row['sha256']:raise ValueError('Imported dataset bytes changed')
                temporary.replace(target)
    return spec

def prepare_schema(engine):
    from src.init_db import Base
    from src.agent.v3.repository import metadata
    from src.etl.provenance_store import metadata as provenance
    from scripts.seed_demo_database import COMPANY_TABLE_SQL
    Base.metadata.create_all(engine);metadata.create_all(engine);provenance.create_all(engine)
    with engine.begin() as conn:conn.execute(text(COMPANY_TABLE_SQL))

def register_reports(engine,reports):
    from src.etl.provenance_store import current,versions,describe_pdf,identity_clause
    from src.agent.v3.repository import identity_digest
    registered=[]
    for row in reports:
        path=(ROOT/row['source_path']).resolve();document=describe_pdf(path)
        if document['source_sha256']!=row['document_version'] or document['page_count']!=row['page_count']:
            raise ValueError('Original PDF differs from the report inventory')
        # 注册不把旧数字写入事实，只保存文件身份及报告选择；抽取与验收随后执行。
        payload=dict(stock_code=row['stock_code'],report_year=row['year'],report_period=row['period'],document=document,
                     facts=[dict(stock_abbr=row['company'])],quality=dict(review_status='registered_not_extracted'))
        registered.append(payload)
    with engine.begin() as conn:
        for payload in registered:
            old=conn.execute(select(current.c.payload).where(identity_clause(payload))).scalar_one_or_none()
            if old and old.get('document',{}).get('source_sha256')==payload['document']['source_sha256']:continue
            identity={k:payload[k] for k in ('stock_code','report_year','report_period')}
            version=identity_digest(payload)
            if not conn.execute(select(versions.c.version_id).where(versions.c.version_id==version)).first():
                conn.execute(versions.insert().values(version_id=version,payload=payload))
            conn.execute(current.delete().where(identity_clause(payload)))
            conn.execute(current.insert().values(**identity,payload=payload))
    return len(registered)

def create_reader():
    import pymysql
    from config.db_config import get_db_config,get_readonly_db_config
    admin=get_db_config();reader=get_readonly_db_config()
    if not re.fullmatch(r'[A-Za-z0-9_]+',admin.database) or not re.fullmatch(r'[A-Za-z0-9_]{1,32}',reader.user):
        raise ValueError('Unsafe database or readonly account name')
    connection=pymysql.connect(host=admin.host,port=admin.port,user=admin.user,password=admin.password,autocommit=True)
    try:
        with connection.cursor() as cursor:
            # 专用账号只授权当前数据库SELECT。已存在账号不降权、不改密码，需预检确认其权限。
            cursor.execute('CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s',(reader.user,'localhost',reader.password))
            tables=('company','income_sheet','balance_sheet','cash_flow_sheet','core_performance_indicators_sheet','financial_data_releases','financial_data_pointer','financial_canonical_reports','financial_canonical_facts','financial_report_provenance','financial_report_versions','financial_report_material')
            cursor.execute('SHOW GRANTS FOR %s@%s',(reader.user,'localhost'))
            grants=[row[0] for row in cursor.fetchall()]
            for grant in grants:
                if 'GRANT USAGE ON *.*' in grant:continue
                if not grant.startswith('GRANT SELECT ON ') or any(token in grant for token in ('WITH GRANT OPTION','*.*')):
                    raise ValueError('现有问答账号权限过大；使用独立只读账号，不自动修改旧账号权限')
                if not any(f'`{admin.database}`.`{table}`' in grant for table in tables):
                    raise ValueError('问答账号含业务表或其他库权限；请使用新的专用账号')
            granted=[];deferred=[]
            for table in tables:
                if table=='financial_report_material':
                    cursor.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s',(admin.database,table))
                    if not cursor.fetchone()[0]:deferred.append(table);continue
                cursor.execute(f'GRANT SELECT ON `{admin.database}`.`{table}` TO %s@%s',(reader.user,'localhost'));granted.append(table)
            return dict(granted=granted,deferred_until_java_migration=deferred)
    finally:connection.close()

def publish_candidate(engine,folder):
    from scripts.accept_canonical_release import validate
    from src.agent.v3.repository import releases,pointer,stage_release,publish_release
    from src.etl.canonical_projection import apply_projections
    manifest,reports,facts,index,acceptance=validate(Path(folder))
    with engine.connect() as conn:
        status=conn.execute(select(releases.c.status).where(releases.c.version==manifest['version'])).scalar_one_or_none()
        serving=conn.execute(select(pointer.c.version).where(pointer.c.name=='serving')).scalar_one_or_none()
    if status=='accepted':
        if serving!=manifest['version']:raise ValueError('Accepted historical version requires an explicit rollback, not initialization')
        (Path(folder)/'acceptance.json').write_text(json.dumps(acceptance,ensure_ascii=False,indent=2),encoding='utf-8')
        return dict(version=serving,facts=len(facts),reports=len(reports),idempotent=True)
    stage_release(engine,manifest['version'],facts,reports,index['collection'],manifest)
    def project(conn):
        apply_projections(conn,facts,reports)
        companies={r['stock_code']:r['company'] for r in reports}
        for code,name in companies.items():
            years=[r['year'] for r in reports if r['stock_code']==code]
            conn.execute(text("INSERT INTO company(stock_code,abbr,dataset_tag,data_status,first_year,last_year) VALUES(:code,:name,'真实财报','imported',:first,:last) ON DUPLICATE KEY UPDATE abbr=VALUES(abbr),dataset_tag=VALUES(dataset_tag),data_status=VALUES(data_status),first_year=VALUES(first_year),last_year=VALUES(last_year)"),dict(code=code,name=name,first=min(years),last=max(years)))
    publish_release(engine,manifest['version'],acceptance,project=project)
    (Path(folder)/'acceptance.json').write_text(json.dumps(acceptance,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(version=manifest['version'],facts=len(facts),reports=len(reports),idempotent=False)

def run_stage(script,env,folder,*extra):
    subprocess.run([sys.executable,str(ROOT/'scripts'/script),'--env',str(env),'--audit' if script!='audit_canonical_facts.py' else '--output',str(folder),*extra],cwd=ROOT,check=True)

def initialize(env,mode='rebuild',package_set=None,output='data/runtime/real-rebuild',create_database=False,reader=False):
    from dotenv import load_dotenv
    load_dotenv(Path(env),override=True)
    from config.db_config import get_db_config
    config=get_db_config()
    if create_database:
        if not re.fullmatch(r'[A-Za-z0-9_]+',config.database):raise ValueError('Unsafe database name')
        server=create_engine(config.connection_string_no_db)
        with server.begin() as conn:conn.execute(text(f'CREATE DATABASE IF NOT EXISTS `{config.database}` CHARACTER SET utf8mb4'))
        server.dispose()
    spec=import_packages(package_set,include_ocr=mode=='rebuild') if package_set else None
    engine=create_engine(config.connection_string,pool_pre_ping=True);prepare_schema(engine)
    reader_permissions=create_reader() if reader else None
    source=ROOT/'data/runtime/real-package'
    reports=json.loads((source/'reports.json').read_text(encoding='utf-8'))
    register_reports(engine,reports)
    folder=(ROOT/output).resolve()
    if not folder.is_relative_to(ROOT/'data/runtime'):raise ValueError('Candidate must be in this installation data/runtime')
    if mode=='rebuild':
        if os.getenv('PDF_TEXT_EXTRACTOR','pymupdf') not in {'pymupdf','pypdf'} and not os.getenv('PDFTOTEXT_EXECUTABLE') and not __import__('shutil').which('pdftotext'):
            raise ValueError('真实重建需安装Poppler，并配置PDFTOTEXT_EXECUTABLE或PATH')
        run_stage('audit_canonical_facts.py',env,folder)
    elif mode=='quick':
        import shutil
        folder.mkdir(parents=True,exist_ok=True)
        for name in ('facts.json','reports.json','narratives.jsonl','manifest.json'):shutil.copyfile(source/name,folder/name)
    else:raise ValueError('Unknown initialization route')
    run_stage('build_canonical_index.py',env,folder)
    receipt=publish_candidate(engine,folder)
    receipt.update(route=mode,originals_registered=len(reports),fresh_original_extraction=mode=='rebuild',package_version=spec['data_version'] if spec else None)
    if reader_permissions is not None:receipt['reader_permissions']=reader_permissions
    (folder/'INSTALL_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    engine.dispose();return receipt
