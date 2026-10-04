# Review resolution

All3Important accepted based code and failing regression. No Critical/Minor findings. Exactly one fix pass, no second reviewer.

1. Pending card: two actual DOM button clicks in cached/uncached recall failed before; after authoritative pending restores complete=false/waiting=true and removes completed feedback, both issue continuation HTTP. page-conversations.js GREEN, old page-smoke ALL OK. Actual browser controlled pending fixture also reloads then clicks through successfully; this proves frontend lifecycle only, not a real model/native backend acceptance.
2. Failed resumed rawtools: test_failed_resume_tools_do_not_join_committed_waiting_turn RED→GREEN, projection takes committed messages only, full checkpoint still preserves failed rawtool pair.
3. Recent repeated order: test_recent_reference_uses_last_mention_of_repeated_order and test_summary_source_proves_last_exact_identifier_occurrence RED→GREEN. Independently retain first/last proven user mentions, exact1001 not10012 substring; tie in selected chronology returnsNone.

Full pass:862 passed5deselected39.42s, Ruffpassed, twoJS ALL OK. References8cases now require impacted real rerun after credit recovery because provenance input changed. Summary/token probes unchanged.

Declined-to-judge rulings:
- actual22-turn/browser gates: keep incomplete until actual evidence, cost of calling them complete would be false acceptance.
- router binding refresh: old router explicitly stale; regenerate32 affected cases once final source frozen and credit restored, cost is necessary upstream calls.
- course user_id identity: retain approved scope with conversation ownership checks; no production login claim, cost if deployed as authentication is insufficient access control outside this course scope.
- no reviewer full-suite/external reruns: author ran fresh862suite and JS; reviewer used read-only reproductions as intended. Cost: external environment still awaits runtime acceptance.

Remaining external402 gate prevents finish/merge readiness. No merge/push or workspace deletion performed.
