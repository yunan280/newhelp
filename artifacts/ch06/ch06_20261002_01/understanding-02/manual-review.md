# Understanding semantic review — run understanding-02

Reviewed 32 labeled outputs against original questions and same-session trusted entities. Automated status 32/32, original/final JSON 32/32, service errors 0. Request and response model deepseek-v4-pro.

- u-001..u-016: complete questions are byte-for-character text passthrough; numeric/conditional facts retained. No inferred condition or order added.
- u-017..u-022: unique known order 1001 supplied; refund/repair/logistics/amount/exchange meanings retained. u-021 preserves the original negative clause after an explicit order prefix.
- u-023..u-025: multiple/absent references remain unresolved and no ID is invented.
- u-026: colloquial question normalized to 如何申请退货？ without adding a reason or order. u-027 keeps the understandable warranty question; no unsupported period added.
- u-028: logistics stagnation is now order_specific; order remains unknown.
- u-029..u-030: original negation/condition attachments preserved after explicit trusted object prefix; no quality fault invented.
- u-031..u-032: explicit order preserved; negated 1002 not chosen.

First run understanding-01 remains retained: 31/32, u-028 scope incorrect. Fixed prompt boundary, no label edits. Prefix fallback is conservative and may retain colloquial wording to avoid changing an asserted fact. This small authored set does not establish real-world universal accuracy.
