from src.agent.v3.request_bindings import explicit_company_set_constraints
from src.agent.v3.agent import V3Agent
from src.agent.v3.contracts import Request,Conditions,Goal,CompanyMention
from src.agent.v3.planner import SemanticReview

COMPANIES={'600085':'同仁堂','002082':'万邦德'}


def test_registered_company_set_commands_keep_identity_and_indicator_negation_is_separate():
    q='把同仁堂去掉，只留万邦德'
    assert explicit_company_set_constraints(q,COMPANIES)=={'keep':{'002082'},'exclude':{'600085'}}
    assert explicit_company_set_constraints('不查同仁堂营收，改看同仁堂归母',COMPANIES)=={'keep':set(),'exclude':set()}
    assert explicit_company_set_constraints('只留同仁堂和万邦德',COMPANIES)['keep']==set(COMPANIES)
    from src.agent.v3.request_bindings import company_set_is_global
    local='只留万邦德，另外查同仁堂'
    assert not company_set_is_global(local,COMPANIES,explicit_company_set_constraints(local,COMPANIES))


def test_independent_model_approval_cannot_publish_reversed_company_removal():
    q='把同仁堂去掉，只留万邦德'
    request=Request(turn_id='t',question=q,conditions=Conditions(codes=['600085'],metrics=['operating_revenue']),
        goals=[Goal(id='lookup',kind='lookup',text=q)],company_mentions=[CompanyMention(text=name,kind='explicit',codes=[code]) for code,name in COMPANIES.items()])
    audit=SemanticReview(goal_requirements=[dict(kind='lookup',goal_ids=['lookup'],covered=True)],satisfied=True,
        clarification=[],planner_defects=[],companies_correct=True,metrics_and_scope_correct=True,time_correct=True,
        goals_correct=True,constraints_correct=True,presentation_correct=True)
    reviewed=V3Agent._review_exact_bindings(request,audit)
    assert not reviewed.accepted and not reviewed.companies_correct
