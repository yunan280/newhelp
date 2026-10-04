# Ch07 frozen evaluations

All cases are synthetic labeled examples. Calibration and acceptance are separate files:
summary 12/12, references 8/8, token calibration 16. Run `python -m mewhelp.ch07.evaluation freeze`
before requesting the upstream model. The SHA256 manifest rejects changed/missing labels.
Do not adjust acceptance labels to hide a failure. Preserve each candidate's results.

Summary grades preserve exact order/phone identifiers, require labeled intent and unresolved
facts, reject invented numbers/approval/old-background contamination, and enforce 30–200
characters (except the fixed empty-business sentence). Every real report retains output,
usage, model, timing, prompt and dataset hashes; the operator also reviews semantic fidelity.
Reference labels specify first/latest orders, ambiguity and negation. Integrated provenance
must reject untrusted objects even if an upstream model guesses one.
Token calibration measures actual input usage across Chinese, ASCII, mixed identifiers,
punctuation, tool payloads and full tool schemas; counter and budget reserves ship together.
