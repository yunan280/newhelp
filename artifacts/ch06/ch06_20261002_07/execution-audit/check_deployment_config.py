import json
from pathlib import Path
from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.intent import load_router_calibration
from mewhelp.ch05.state import WorkflowContext
from mewhelp.ch06.config import Ch06Settings, PolicyCalibration

records = []
for mode in ['primary', 'cascade']:
    settings = Ch06Settings(cascade_enabled=mode == 'cascade', small_model='deepseek-flash' if mode == 'cascade' else None,
                           calibration_path=Path(f'artifacts/ch06/ch06_20261002_07/calibration-{mode}-reused/router.json'))
    context = WorkflowContext(lambda: None, lambda: None, lambda: None, Ch05Settings().limits(), router_settings=settings)
    calibration = load_router_calibration(context)
    records.append({'mode': mode, 'configuration_valid': True, 'min_confidence': calibration.intent_min_confidence,
                    'upgrade_threshold': calibration.cascade_upgrade_threshold, 'new_model_calls': 0})
policy = PolicyCalibration.model_validate_json(Path('artifacts/ch06/ch06_20261002_01/policy-calibration-02/policy.json').read_text(encoding='utf-8'))
record = {'router_modes': records, 'policy_threshold': policy.policy_rerank_threshold, 'validation': 'strict configuration/hash validation only; no API/model/e2e rerun'}
with Path('artifacts/ch06/ch06_20261002_07/deployment-config.json').open('x', encoding='utf-8') as stream:
    json.dump(record, stream, ensure_ascii=False, indent=2)
print(record)
