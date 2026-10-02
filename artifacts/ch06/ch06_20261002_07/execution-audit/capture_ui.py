import sqlite3,json
from pathlib import Path
ui=Path('artifacts/ch06/ch06_20261002_02/ui')
db=sqlite3.connect('.cache/ch06/acceptance-01/business.sqlite'); db.row_factory=sqlite3.Row
refund=dict(db.execute('select application_no,offer_id,conversation_id,user_id,order_id,reason,status from refund_applications').fetchone())
conv=dict(db.execute('select session_id,user_id from conversations where id=?',(refund['conversation_id'],)).fetchone())
messages=[dict(r) for r in db.execute('select role,content from messages where conversation_id=? order by id',(refund['conversation_id'],))]
report={'scope':'isolated_acceptance_sqlite_real_model_real_milvus','url':'http://127.0.0.1:9006/','session':conv,'receipt':refund,'refund_count':db.execute('select count(*) from refund_applications').fetchone()[0],'original_user_message_count':sum(r['role']=='user' and r['content']=='这个能退吗' for r in messages),'messages':messages,'observed_browser_checks':['missing_order_cards','refresh_pending_cards','same_message_resume_answer_with_citations','empty_reason_submit_disabled','cancel_no_application','select_fixed_reason_submit_pending','refresh_server_receipt','new_logistics_expires_old_card','refresh_old_card_not_active'],'screenshots':sorted(p.name for p in ui.glob('*.png'))}
assert report['refund_count']==1 and report['original_user_message_count']==1
(ui/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('SQL applications=1; original user message=1; browser session='+conv['session_id'])
