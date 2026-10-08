import json

from mewhelp.ch05.limits import TokenUsage
from mewhelp.ch05.schemas import IntentOutput
from mewhelp.ch06 import evaluation, structured


async def test_calibration_passes_live_catalog_to_classifier(tmp_path, monkeypatch):
    """A wrong bridge argument must fail calibration, not silently omit capabilities."""
    data = tmp_path / 'dataset'
    data.mkdir()
    case = {'id': 'cal-1', 'split': 'calibration', 'question': '查在保',
            'expected': {'intent': '售后'}}
    (data / 'calibration.jsonl').write_text(json.dumps(case, ensure_ascii=False) + '\n', encoding='utf-8')
    evaluation.freeze_dataset(data)
    monkeypatch.setenv('MODEL_CONTEXT_WINDOW', '32768')
    catalog = [{'name': 'query_warranty', 'description': '查订单在保', 'parameters': {}}]
    requests = []

    async def provider(context, state, **kwargs):
        requests.append(kwargs['messages'])
        return structured.StructuredCall(IntentOutput(intent='售后', confidence=.99,
            matched_tool='query_warranty'), None, [], TokenUsage(), {'classifier': 1})

    monkeypatch.setattr(structured, 'invoke_json', provider)
    output = tmp_path / 'out'
    assert await evaluation.calibrate_router(data, output, tool_catalog=catalog) == 0
    assert requests and 'query_warranty' in ''.join(str(message.content) for message in requests[0])
    assert json.loads((output / 'router.json').read_text())['sample_count'] == 1
