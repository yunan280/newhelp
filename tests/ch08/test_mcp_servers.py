from mewhelp.tools.business import build_business_tools


async def test_server_mock_tools_and_schema_are_independent():
    from mewhelp.ch08.mcp_servers.logistics import build_server
    from mewhelp.ch08.mcp_servers.aftersales import build_server as aftersales
    logistics = build_server(port=9021)
    tools = await logistics.list_tools()
    assert [t.name for t in tools] == ['query_logistics']
    assert tools[0].inputSchema['properties']['order_id']['minLength'] == 1
    assert {t.name for t in await aftersales(port=9022).list_tools()} == {'query_warranty', 'query_return_progress'}
    assert 'query_logistics' not in {t.name for t in build_business_tools()}


def test_mock_data_aligns_with_demo_and_is_reproducible():
    from mewhelp.ch08.mcp_servers.mock_data import logistics_data, warranty_data, return_data
    assert logistics_data('1001') == logistics_data('1001')
    assert logistics_data('1001')['data']['status'] == 'SIGNED'
    assert logistics_data('1003')['data']['status'] == 'UNSHIPPED'
    assert logistics_data('1001') != logistics_data('2002')
    assert warranty_data('1001')['data']['order_id'] == '1001'
    assert return_data('R1001')['data']['return_id'] == 'R1001'
