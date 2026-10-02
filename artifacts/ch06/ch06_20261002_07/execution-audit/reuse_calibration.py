"""Offline replay of the unchanged classifier calibration inputs; no model calls."""
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from mewhelp.ch06.config import Ch06Settings, RouterCalibration
from mewhelp.ch06.evaluation import model_hash, runtime_hash, select_router_thresholds, verify_dataset
from mewhelp.ch06.prompts import INTENT_SYSTEM

BASE = '6567d76'
old_dir, out_dir = map(Path, sys.argv[1:3])


def historical(path):
    return subprocess.check_output(['git', 'show', f'{BASE}:{path}']).decode('utf-8')


def definition(source, name):
    return ast.dump(next(node for node in ast.parse(source).body if getattr(node, 'name', None) == name), include_attributes=False)


unchanged = []
for path in ['src/mewhelp/ch06/config.py', 'src/mewhelp/ch06/structured.py',
             'src/mewhelp/ch05/schemas.py', 'src/mewhelp/ch05/limits.py',
             'src/mewhelp/ch05/config.py', 'src/mewhelp/ch05/state.py',
             'src/mewhelp/ch05/intent.py', 'src/mewhelp/llm.py']:
    assert historical(path) == Path(path).read_text(encoding='utf-8'), f'measured dependency changed: {path}'
    unchanged.append(path)
prompt_source = historical('src/mewhelp/ch06/prompts.py')
old_prompt = next(ast.literal_eval(node.value) for node in ast.parse(prompt_source).body
                  if isinstance(node, ast.Assign) and any(getattr(target, 'id', None) == 'INTENT_SYSTEM' for target in node.targets))
assert old_prompt == INTENT_SYSTEM
for name in ['calibrate_router', 'select_router_thresholds', 'model_hash']:
    assert definition(historical('src/mewhelp/ch06/evaluation.py'), name) == definition(Path('src/mewhelp/ch06/evaluation.py').read_text(encoding='utf-8'), name)
manifest = verify_dataset(Path('eval/ch06'))
expected = [json.loads(line) for line in Path('eval/ch06/calibration.jsonl').read_text(encoding='utf-8').splitlines()]
rows = [json.loads(line) for line in (old_dir / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
assert [row['case'] for row in rows] == expected and len(rows) == 32
settings = Ch06Settings()
old = RouterCalibration.model_validate_json((old_dir / 'router.json').read_text(encoding='utf-8'))
assert old.model_hash == model_hash(settings) and old.dataset_hash == manifest['dataset_hash']
for row in rows:
    for key, model in [('primary', settings.primary_model), ('small', settings.small_model)]:
        if model is not None:
            assert row[key]['actual'] and row[key]['raw_responses'][-1]['parsed']
            assert row[key]['raw_responses'][-1]['request_model'] == model
selected, _ = select_router_thresholds(rows, has_small=bool(settings.small_model))
assert selected[:2] == (0, 0)
assert (selected[3], selected[4]) == (old.intent_min_confidence, old.cascade_upgrade_threshold)
current = old.model_copy(update={'understanding_hash': runtime_hash('understanding', settings=settings),
                                'intent_hash': runtime_hash('intents', settings=settings)})
out_dir.mkdir(parents=True, exist_ok=False)
(out_dir / 'router.json').write_text(current.model_dump_json(indent=2), encoding='utf-8')
record = {'method': 'offline_replay_of_unchanged_classifier_calibration', 'new_model_calls': 0,
          'source_results': str(old_dir / 'results.jsonl'),
          'source_sha256': hashlib.sha256((old_dir / 'results.jsonl').read_bytes()).hexdigest(),
          'baseline_measured_dependencies': BASE, 'unchanged_measured_dependencies': unchanged,
          'intent_prompt_unchanged': True, 'calibration_algorithm_unchanged': True,
          'model_hash': old.model_hash, 'dataset_hash': old.dataset_hash, 'sample_count': len(rows),
          'selected': selected, 'thresholds_unchanged': True,
          'binding_only': ['intent_hash', 'understanding_hash'],
          'limitation': 'Understanding guards changed. This does NOT certify current end-to-end model accuracy. User explicitly requested no repeated model runs.'}
(out_dir / 'reuse-audit.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print({'reused': len(rows), 'new_model_calls': 0, 'thresholds': selected[3:]})
