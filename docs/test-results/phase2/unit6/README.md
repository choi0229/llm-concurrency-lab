# Phase 2 Unit 6 — Formal Benchmark 상태

## Official Formal Valid Dataset: 0/18

target matrix: P-E × {R3,R6,R8} × 3 rep + VT-Limited × {R3,R6,R8} × 3 rep = **18 runs**, 전부 미확보.

## Prior Mac attempts: 5 discarded

아래 5건은 모두 `discarded/`로 이동, 공식 집계에서 완전히 제외한다. 원본 raw 데이터는 삭제하지 않고
보존한다(§ 아래 "Mac + Docker Desktop = Formal Environment Rejected").

| 디렉터리 (in `discarded/`) | 판정 | 사유 |
|---|---|---|
| `pe-r3-run1-attempt2-INVALID_FOR_FORMAL-diagnostic-only` | INVALID_FOR_FORMAL(진단/참고용) | outcome cohort는 깨끗함(901/901 completed, 0 timeout/reject/fail) — 48초 stall이 throughput population을 오염시킴(target≈3.0 RPS 대비 gateway_received≈2.49 RPS). |
| `pe-r3-run1-attempt1-INVALID-stall-cohort-damaged` | INVALID | 234초 gap, outcome cohort 자체가 손상(timeout/failure 다수). |
| `pe-r3-run2-attempt1-INVALID-stall-cohort-damaged` | INVALID | 132+50+63+85+247초 gap 누적, 69건 failure. |
| `pe-r3-run2-attempt2-INVALID-stall-cohort-damaged` | INVALID | 201+88초 gap, 20건 failure. |
| `pe-r3-canary-run1-MAC_ENV_REJECTED-post-reboot-stall-confirmed` | Canary FAIL | 호스트 완전 재부팅 후 재실행. outcome cohort는 깨끗함(901/901 completed) — 그러나 `gateway_request_received_total`/`mockllm_completed_requests_total`이 k6 arrival 중 약 30~35초간 동시 plateau, throughput population 재오염(`gateway_received_rate`≈2.63, `measurement_window_completion_rate`≈2.553 vs target 3.0). |

## Mac + Docker Desktop = Formal Environment Rejected

호스트 완전 재부팅 후에도 위 Canary가 동일한 stall 신호(로그 gap + 독립 Prometheus counter plateau)로
실패했다. Mac + Docker Desktop에서는 Phase 2 Formal Benchmark를 더 이상 재시도하지 않는다.

근거·root cause 표현 원칙·이후 절차 전체는 `docs/decisions/phase2-formal-linux-environment.md`가
단일 authoritative source다 — 이 README에서 중복 서술하지 않는다.

## Superseded (2026-08-19): Linux 경로 대신 Native macOS ARM64로 완료됨

이 문서가 이전에 계획했던 "Linux Formal host 준비 → Linux Stability Canary → 18 Formal Runs" 경로는
**진행되지 않았다** — 사용자가 유료 Cloud를 쓰지 않기로 결정하면서, Docker Desktop을 완전히 제거한
**native macOS ARM64 harness**가 최종 대안으로 채택·실행됐다. 이 디렉터리(`unit6/`)의 5건 discarded
Mac+Docker attempt는 그대로 이력으로 보존하지만, **공식 18-run Formal dataset은 이 디렉터리가 아니라
`docs/test-results/phase2/unit6-native/`에 있다.**

- Native 환경 설계·채택 과정: [`docs/decisions/phase2-formal-native-macos-environment.md`](../../../decisions/phase2-formal-native-macos-environment.md)
- 18 valid run + canonical aggregate: `docs/test-results/phase2/unit6-native/`
- 최종 보고서: [`docs/test-results/phase2/phase2-final-report.md`](../phase2-final-report.md)

## 참고 스크립트

- `scripts/run-phase2-formal-benchmark.sh` — 단일 run 실행(preflight/fresh stack/warmup/measurement/
  drain/postflight/stall_check/collect).
- `scripts/run-phase2-formal-matrix.sh` — 18-run 배치 드라이버(고정 순서, config별 그룹).
- `scripts/collect_phase2_formal_result.py` — throughput population vs outcome cohort 분리 집계,
  k6-vs-Prometheus cross-check.

프로토콜 전체: `docs/test-plan/phase2-formal-protocol.md`. 환경 이전 결정: `docs/decisions/
phase2-formal-linux-environment.md`.
