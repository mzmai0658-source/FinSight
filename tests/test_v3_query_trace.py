from sqlalchemy import create_engine
from src.agent.v3.repository import CanonicalRepository,metadata,releases,pointer,facts,reports
from tests.test_v3_contracts import fact


def test_trace_records_the_executed_parameterized_selection_and_reference_queries():
    engine=create_engine('sqlite://')
    metadata.create_all(engine)
    value=fact('600085',2024,'100',metric='operating_revenue')
    with engine.begin() as conn:
        conn.execute(releases.insert().values(version='v',status='accepted',index_collection='test',manifest={}))
        conn.execute(pointer.insert().values(name='serving',version='v'))
        conn.execute(facts.insert().values(data_version='v',id=value.id,stock_code=value.stock_code,year=2024,period='FY',metric=value.metric,
            scope=value.scope,status=value.status,payload=value.model_dump(mode='json')))
    repo=CanonicalRepository(engine)
    found=repo.query(['600085'],{'600085':[(2024,'FY')]},['operating_revenue'],'consolidated')
    trace=repo.query_trace[0]
    assert [item.id for item in found]==[value.id]
    assert trace['purpose']=='financial_facts' and trace['rows']==1 and trace['status']=='completed'
    assert 'SELECT financial_canonical_facts.payload' in trace['sql'] and '600085' not in trace['sql']
    assert '600085' in trace['parameters'].values() and 2024 in trace['parameters'].values()
    assert repo.by_ids([value.id])==found and repo.query_trace[-1]['purpose']=='fact_references'
    assert repo.query_trace[-1]['parameters']!=trace['parameters']
