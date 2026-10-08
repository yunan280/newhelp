import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from mewhelp.ch09.migration import migrate_ch09
from mewhelp.db.models import EvalRun, ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion

pytestmark = pytest.mark.mysql


def test_mysql_authority_defaults_enum_json_fk_and_idempotency(ch09_mysql):
    engine = ch09_mysql
    inspector = inspect(engine)
    review = {c['name']: c for c in inspector.get_columns('review_queue')}
    assert list(review) == ['id', 'normalized_question', 'ai_suggested_answer', 'occurrence_count',
                           'review_status', 'approved_answer', 'created_at', 'updated_at']
    assert review['id']['type'].unsigned
    assert review['normalized_question']['type'].length == 512
    assert review['occurrence_count']['type'].unsigned
    assert review['review_status']['type'].enums == ['待审', '通过', '驳回']
    assert review['review_status']['default'] == "'待审'"
    assert review['occurrence_count']['default'].strip("'") == '1'
    runs = {c['name']: c for c in inspector.get_columns('eval_runs')}
    assert list(runs) == ['id', 'triggered_by', 'dataset_size', 'metrics', 'created_at']
    assert runs['triggered_by']['type'].enums == ['定时', '手动']
    assert runs['triggered_by']['default'] == "'定时'"
    assert runs['dataset_size']['type'].unsigned
    assert str(runs['metrics']['type']) == 'JSON'
    foreign = next(f for f in inspector.get_foreign_keys('low_confidence_questions')
                   if f['name'] == 'fk_lcq_review')
    assert foreign['referred_table'] == 'review_queue'
    assert foreign['constrained_columns'] == ['matched_review_id']
    assert foreign['options']['ondelete'] == 'SET NULL'
    assert 'idx_review_status' in {i['name'] for i in inspector.get_indexes('review_queue')}
    assert 'idx_created_at' in {i['name'] for i in inspector.get_indexes('eval_runs')}
    assert 'idx_matched_review_id' in {i['name'] for i in inspector.get_indexes('low_confidence_questions')}
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        queue = ReviewQueue(normalized_question='拆封后还能退吗？', ai_suggested_answer='人工核实')
        session.add(queue)
        session.flush()
        pool = LowConfidenceQuestion(original_question='拆了还能退不', entry_point='feedback',
            trigger_stage='feedback', reason_code='user_feedback', reason='用户反馈未解决',
            matched_review_id=queue.id, retrieved_chunks=None)
        run = EvalRun(triggered_by='手动', dataset_size=40, metrics={'说明': '中文', 'mrr': .71})
        session.add_all([pool, run])
        session.commit()
        assert queue.review_status == '待审' and queue.occurrence_count == 1
        assert session.scalar(text('SELECT retrieved_chunks IS NULL FROM low_confidence_questions')) == 1
        session.delete(queue)
        session.commit()
        session.expire_all()
        assert session.get(LowConfidenceQuestion, pool.id).matched_review_id is None
        assert session.get(EvalRun, run.id).metrics['说明'] == '中文'
    assert migrate_ch09(engine)['changed'] == []
    assert migrate_ch09(engine)['changed'] == []
    with factory() as session:
        assert session.scalars(select(LowConfidenceQuestion)).one().original_question == '拆了还能退不'


@pytest.mark.parametrize('bad_ddl', [
    'ALTER TABLE review_queue MODIFY normalized_question VARCHAR(100) NOT NULL',
    "ALTER TABLE review_queue MODIFY review_status ENUM('待审','通过') NOT NULL DEFAULT '待审'",
    'ALTER TABLE low_confidence_questions DROP FOREIGN KEY fk_lcq_review',
])
def test_incompatible_mysql_existing_objects_raise(ch09_mysql, bad_ddl):
    with ch09_mysql.begin() as connection:
        connection.exec_driver_sql(bad_ddl)
        if 'DROP FOREIGN KEY' in bad_ddl:
            connection.exec_driver_sql('ALTER TABLE low_confidence_questions ADD CONSTRAINT fk_lcq_review '
                'FOREIGN KEY (matched_review_id) REFERENCES review_queue (id) ON DELETE CASCADE')
    with pytest.raises(ValueError, match='incompatible'):
        migrate_ch09(ch09_mysql)
