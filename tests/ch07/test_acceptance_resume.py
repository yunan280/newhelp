import json
from pathlib import Path

import pytest

from scripts.smoke_ch07_acceptance import dialogue


def test_resume_reuses_only_verified_original_successes(tmp_path):
    from scripts.smoke_ch07_acceptance import verified_resume
    questions = dialogue(22)
    report = {'profile':'demo','session_id':'ch07-resume-demo','user_id':'eval',
              'conversation_id':3, 'turns':[{'round':1,'question':questions[0],
                  'result':{'answer':'已签收','stop_reason':'completed'}},
                  {'round':2,'question':questions[1],'error':'APIStatusError402'}]}
    path = tmp_path/'http.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    snapshot = {'messages':[{'role':'user','content':questions[0]},
                            {'role':'assistant','content':'已签收'}]}
    restored = verified_resume(path, questions=questions, profile='demo',
        session_id='ch07-resume-demo', user_id='eval', snapshot=snapshot)
    assert len(restored['turns']) == 1
    assert restored['prior_errors'] == ['APIStatusError402']
    assert restored['resumed_from'] == str(path.resolve())


@pytest.mark.parametrize('change', ['user', 'profile', 'question', 'original'])
def test_resume_rejects_wrong_owner_or_changed_inputs(tmp_path, change):
    from scripts.smoke_ch07_acceptance import verified_resume
    questions = dialogue(22)
    report = {'profile':'demo','session_id':'ch07-resume-demo','user_id':'eval',
              'conversation_id':3, 'turns':[{'round':1,'question':questions[0],
                  'result':{'answer':'已签收','stop_reason':'completed'}}]}
    snapshot = {'messages':[{'role':'user','content':questions[0]},
                            {'role':'assistant','content':'已签收'}]}
    if change == 'user': report['user_id'] = 'foreign'
    if change == 'profile': report['profile'] = 'default'
    if change == 'question': report['turns'][0]['question'] = '更改标签'
    if change == 'original': snapshot['messages'][1]['content'] = '更改原文'
    path = Path(tmp_path/'http.json'); path.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError, match='resume'):
        verified_resume(path, questions=questions, profile='demo',
            session_id='ch07-resume-demo', user_id='eval', snapshot=snapshot)
