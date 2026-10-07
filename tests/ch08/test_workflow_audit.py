import time
from dataclasses import replace

from sqlalchemy import select

from mewhelp.ch05.workflow import load_order_node
from mewhelp.db.models import ToolAuditLog
from tests.ch08.test_ticket_graph import install_tools


async def test_fixed_load_order_uses_hidden_audited_tool(workflow_runtime, tmp_path, session_factory):
    tools = await install_tools(workflow_runtime, tmp_path, session_factory)
    snapshot = await tools.refresh()
    assert snapshot.get('load_order') is not None
    assert 'load_order' not in {item['name'] for item in snapshot.catalog()}
    context = replace(workflow_runtime.context, tool_snapshot=snapshot)
    state = {'user_id':'demo-user', 'session_id':'fixed-order', 'conversation_id':None,
        'selected_order_id':'1001', 'turn_id':'fixed', 'started_at':time.time(), 'tool_trace':[]}
    result = await load_order_node(state, context, lambda event:None)
    assert result['order']['order_id'] == '1001'
    with session_factory() as db:
        audit = db.scalars(select(ToolAuditLog)).one()
        assert audit.tool_name == 'load_order' and audit.status == '成功'
