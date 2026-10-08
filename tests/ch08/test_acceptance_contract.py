import pytest


def evidence():
    audit = {'tool_call_id':'call-1','tool_name':'query_logistics','tool_source':'mcp',
        'mcp_server':'logistics','status':'成功','retry_count':0,'duration_ms':20,'created_at':'2026-10-07'}
    return [
        {'requirement':1,'tool_used':True,'registration_only':True,'audits':[dict(audit,tool_source='builtin',mcp_server=None)]},
        {'requirement':2,'servers':['logistics','aftersales'],'answers_grounded':True,'audits':[audit,dict(audit,mcp_server='aftersales')]},
        {'requirement':3,'tool_used':True,'customer_pid_unchanged':True,'customer_code_unchanged':True,'server_restarted':True,'audits':[audit]},
        {'requirement':4,'clarified':True,'preview':True,'tickets_delta':1,'ticket_no':'T123','answer':'工单已提交 T123','audits':[dict(audit,tool_name='create_ticket',tool_source='builtin',mcp_server=None)]},
        {'requirement':5,'preview':True,'tickets_delta':0,'audits':[dict(audit,tool_name='create_ticket',tool_source='builtin',mcp_server=None,status='权限拒绝')]},
        {'requirement':6,'honest_failure':True,'audits':[dict(audit,status='超时',retry_count=2),dict(audit,tool_name='create_ticket',tool_source='builtin',mcp_server=None,status='超时',retry_count=0)]},
    ]


def report(rows):
    try:
        from mewhelp.ch08.evaluation import validate_acceptance
    except ImportError:
        pytest.fail('验收报告缺少失败合同')
    return validate_acceptance(rows)


def test_complete_evidence_passes():
    assert report(evidence())['passed']


@pytest.mark.parametrize('field', ['tool_call_id','tool_source','duration_ms','created_at'])
def test_missing_audit_fields_fail(field):
    rows=evidence()
    del rows[0]['audits'][0][field]
    assert not report(rows)['passed']


def test_missing_requirement_or_wrong_ticket_delta_fails():
    assert not report(evidence()[:-1])['passed']
    rows=evidence();rows[3]['tickets_delta']=0
    assert not report(rows)['passed']


def test_model_success_claim_without_matching_receipt_fails():
    rows=evidence();rows[3]['answer']='工单已创建 T-other'
    assert not report(rows)['passed']


def test_write_retry_or_wrong_read_timeout_status_fails():
    rows=evidence();rows[5]['audits'][1]['retry_count']=1
    assert not report(rows)['passed']
    rows=evidence();rows[5]['audits'][0]['status']='成功'
    assert not report(rows)['passed']
