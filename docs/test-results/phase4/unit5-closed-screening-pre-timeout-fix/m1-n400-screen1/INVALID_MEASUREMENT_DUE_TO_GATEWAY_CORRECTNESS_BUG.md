# This run is INVALID_MEASUREMENT_DUE_TO_GATEWAY_CORRECTNESS_BUG

Status: NOT `MODEL_TIMEOUT`, NOT `MODEL_REJECTION`, NOT usable as MSC/MRC evidence for M1.

`result.json`/`validity.json` in this directory are preserved exactly as originally collected
(not mutated) — this file is a pointer, added after the fact, explaining what they mean.

## What actually happened

The collector's `prewarm_contamination_ok` check failed (`server_wave_completed_estimate=399` vs
client `completed=350`), which correctly flagged the run as measurement-invalid — but investigation
found this was not a prewarm issue at all. It exposed a genuine correctness bug in
`BlockingMockLlmRelay.java` (shared M1/M2 code): when the absolute 60s deadline is observed by the
relay's own read loop (`isPastDeadline()`) before `DeadlineWatchdog`'s async callback has set the
terminal state, the relay loop broke out but then fell through to `channel.markProducerDone()` —
incorrectly signaling normal completion for a stream that was actually abandoned mid-read. This
misclassified up to 49 genuinely-truncated requests as `completed` server-side.

Three independent observers cross-confirmed the same 50 affected requests:
- Client (k6): `completed=350`, `failed_mid_stream=50`
- Mock (independent process): `mockllm_completed_requests_total=355`,
  `mockllm_cancelled_requests_total=50`
- Gateway server outcome (before the bug was understood): `completed≈399`, `timeout=1` — the
  misclassification itself, now explained.

Full root-cause writeup, fix, and regression evidence: see the Unit 5.1 report and
`docs/decisions/phase4-closed-measurement-semantics.md` (or equivalent Unit 5.1 documentation).

## Disposition

- This run's raw artifacts are preserved as history (this directory, under
  `unit5-closed-screening-pre-timeout-fix/`), not deleted.
- It is excluded from any canonical Screening MSC/MRC bracket calculation.
- M1's post-fix N=400 (and all other N) will be re-measured under a new binary/source revision in
  a fresh screening epoch (`docs/test-results/phase4/unit5-closed-screening/`).
