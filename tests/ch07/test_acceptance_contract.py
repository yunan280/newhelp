from importlib import import_module

import pytest


def grader():
    try:
        return import_module('scripts.smoke_ch07_acceptance').grade_report
    except ModuleNotFoundError:
        pytest.fail('acceptance must require actual SSE, usage, MySQL and context log evidence')


def evidence(profile):
    return {'profile':profile, 'turns_completed':22, 'real_http_sse':True,
        'real_usage':True, 'mysql_originals_match':True, 'mysql_visible_rows':44,
        'history_contexts':22, 'model_contexts':88, 'window_violations':0,
        'degrades':0, 'summary_triggers':0, 'summary_starts':0, 'summary_segments':0,
        'immutable_segments':True, 'monotonic_anchors':True,
        'first_order_reference_ok':True, 'first_summary_injected':False,
        'nonblocking_timestamps':[], 'errors':[]}


def test_report_cannot_pass_with_only_health_or_mock():
    assert grader()({'profile':'default', 'health':'ok', 'turns_completed':22})
    row = evidence('default')
    row['real_usage'] = False
    assert grader()(row)


def test_default_report_rejects_any_compression():
    row = evidence('default')
    assert grader()(row) == []
    row['degrades'] = 1
    assert grader()(row)


def test_demo_requires_cascade_first_order_and_nonblocking_proof():
    row = evidence('demo')
    assert grader()(row)
    row.update(degrades=2, summary_triggers=1, summary_starts=1, summary_segments=1,
               first_summary_injected=True, nonblocking_timestamps=[{'foreground_ms':1,'summary_blocked_until_after_reply':True}])
    assert grader()(row) == []
    row['first_order_reference_ok'] = False
    assert grader()(row)
