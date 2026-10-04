# Independent whole-branch review

Reviewer: gpt-6-astra, fresh context, read-only. Range6d7603c..f6aa8b8.

Strengths: independent summary Session/fixed snapshot/atomic anchor; rawState separate projection; real budget preflight; owner-scoped GET and stale-response protection; incomplete acceptance reported honestly.

Critical: none.
Important:
1. index.html:1259 and931–938: recalled waiting answer complete=true causes picker click814 to return, both cached/uncached. Restore waiting lifecycle based authoritative pending.
2. projection.py:72: committed waiting and failed resume share turn_id; all grouped messages includes new committed=false rawtools. Filter committed set only; checkpoint raw intact.
3. provenance.py:65–78: A1001→B1002→A1001 then recent order returnsB because first-mention positions also used as latest. Keep exact first/last mention evidence, including summary provenance.
Minor: none.

Declined to judge: actual22turn/browser gates402 blocked, not passed; router hash refreshpending, old router not current; existing course user_id auth retained as approved scope; no whole-suite/external reruns by reviewer, only read-only in-memory reproductions.
Assessment: not ready to merge due3Important and pending runtime gate.
