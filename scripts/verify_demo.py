"""作品说明：公开演示采用生产 v3 事实、原页与检索索引；检查隔离只读权限。"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT_DIR=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT_DIR))

def verify_demo():
    from sqlalchemy import create_engine,text
    from config.db_config import get_readonly_db_config
    from src.agent.v3.repository import CanonicalRepository
    from src.agent.v3.evidence import checked_source
    from src.etl.release_profiles import check_report_coverage
    from src.agent.providers import resolve_embedding_function
    from src.agent.tool_shared import resolve_chroma_db_path
    import chromadb
    engine=create_engine(get_readonly_db_config().connection_string)
    repository=CanonicalRepository(engine)
    profile=check_report_coverage(repository.manifest,repository.report_catalog())
    assert profile.kind=='synthetic','该入口只检查公开合成演示版本'
    values=repository.query(['990001'],{'990001':[(2024,'FY')]},['operating_revenue'],'consolidated')
    assert len(values)==1 and str(values[0].decimal)=='150000000.00' and checked_source(values[0])
    with engine.connect() as conn:
        grants=[str(r[0]).upper() for r in conn.execute(text('SHOW GRANTS'))]
    assert all(not any(word in g for word in ('ALL PRIVILEGES','INSERT','UPDATE','DELETE','CREATE','DROP','ALTER','GRANT OPTION')) for g in grants)
    embedding=resolve_embedding_function('query')
    collection=chromadb.PersistentClient(path=str(resolve_chroma_db_path())).get_collection(repository.collection)
    result=collection.query(query_embeddings=embedding.embed_documents(['星河医药2024年营业收入变化原因']),n_results=2,
        where={'$and':[{'stock_code':'990001'},{'year':2024}]},include=['documents','metadatas'])
    assert result['ids'][0] and all(m['stock_code']=='990001' and m['year']==2024 for m in result['metadatas'][0])
    return dict(version=repository.version,profile=profile.public_metadata(),numeric=True,original_page=True,readonly=True,index_chunks=collection.count(),retrieval=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--env-file',type=Path,default=ROOT_DIR/'.env.demo');args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file,override=True)
    print(json.dumps(verify_demo(),ensure_ascii=False,indent=2))
