import json
from pathlib import Path

import pytest


def test_generation_labels_are_frozen_before_model_run():
    from mewhelp.ch09.prompt_eval import load_generation_samples
    path = Path('eval/ch09/generation-samples.jsonl')
    rows = load_generation_samples(path)
    assert len(rows) >= 12 and {r['answerable'] for r in rows} == {True, False}
    assert all(r['label_basis'].strip() for r in rows)
    assert {'wrong-model','explicit-negative','shipping-number','citation-injection'} <= {r['id'] for r in rows}


def test_duplicate_prompt_sample_ids_are_rejected(tmp_path):
    from mewhelp.ch09.prompt_eval import load_generation_samples
    row = json.loads(Path('eval/ch09/generation-samples.jsonl').read_text(encoding='utf-8').splitlines()[0])
    path = tmp_path/'bad.jsonl'
    path.write_text((json.dumps(row)+'\n')*2,encoding='utf-8')
    with pytest.raises(ValueError,match='duplicate'):
        load_generation_samples(path)
