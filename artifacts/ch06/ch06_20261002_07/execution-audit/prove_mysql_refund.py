import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.runtime import open_runtime
from mewhelp.ch05.schemas import RefundRequest, TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch06.refunds import read_refund_receipt, submit_refund
from mewhelp.config import get_settings
from mewhelp.db.engine import SessionLocal
from mewhelp.db.models import RefundApplication
from mewhelp.knowledge.answering import get_rag_runtime

USER = 'ch06-mysql-proof-05-user'
SESSION = 'ch06-mysql-proof-05'
OUT = Path('artifacts/ch06/ch06_20261002_05/mysql-refund-recovery.json')


def count():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(RefundApplication).where(RefundApplication.user_id == USER))


async def main():
    assert not OUT.exists() and count() == 0
    settings = Ch05Settings(checkpoint_path=Path('.cache/ch06/mysql-proof-05/checkpoints.sqlite3'))
    router = Ch06Settings(calibration_path=Path('artifacts/ch06/ch06_20261002_05/calibration-primary/router.json'),
                          policy_calibration_path=Path('artifacts/ch06/ch06_20261002_01/policy-calibration-02/policy.json'))
    async with open_runtime(SessionLocal, settings=settings, router_settings=router,
                            rag_factory=lambda: get_rag_runtime(SessionLocal,
                                collection='ch06_eval_mysql_20261002_01', calibration_path=get_settings().rag_calibration_path)) as runtime:
        turn = await run_turn(runtime, TurnRequest(message='订单1001能退吗', session_id=SESSION, user_id=USER))
        assert turn.status == 'completed' and turn.refund_offer and turn.assessment.verdict == 'eligible'
        request = RefundRequest(session_id=SESSION, user_id=USER, offer_id=turn.refund_offer.offer_id,
                                order_id='1001', reason='七天无理由', confirmed=True)
        async def unavailable(*args, **kwargs):
            raise OSError('ch06 acceptance: checkpoint unavailable AFTER MySQL commit')
        original_update = runtime.graph.aupdate_state
        original_get = runtime.graph.aget_state
        runtime.graph.aupdate_state = unavailable
        try:
            await submit_refund(runtime, request)
            raise AssertionError('fault not reached')
        except OSError as error:
            fault = str(error)
        assert count() == 1
        runtime.graph.aget_state = unavailable
        replay = await submit_refund(runtime, request)
        assert replay.replayed and replay.status == 'pending' and count() == 1
        receipt = read_refund_receipt(runtime.context, request.offer_id, SESSION, USER)
        assert receipt.application_no == replay.application_no
        runtime.graph.aupdate_state = original_update
        runtime.graph.aget_state = original_get
        with SessionLocal() as db:
            row = db.scalar(select(RefundApplication).where(RefundApplication.user_id == USER))
            stored = {column.name: getattr(row, column.name) for column in RefundApplication.__table__.columns}
        record = {'database': 'actual MySQL localhost:3307/mewhelp', 'model': router.primary_model,
                  'policy_collection': 'ch06_eval_mysql_20261002_01', 'turn': turn.model_dump(mode='json'),
                  'explicit_test_confirmation': request.model_dump(mode='json'), 'fault': fault,
                  'first_committed_count': 1, 'replay_count': count(), 'replay': replay.model_dump(mode='json'),
                  'receipt_without_checkpoint': receipt.model_dump(mode='json'), 'persisted_application': stored, 'passed': True}
        with OUT.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, default=str)
        print({'mysql_rows': count(), 'replayed': replay.replayed, 'application_no': replay.application_no, 'passed': True})


asyncio.run(main())
