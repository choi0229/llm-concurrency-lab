# Unit 5.6 — Continuous Formal Harness Verification

**기능 검증용. Formal dataset(18-run)에는 포함하지 않는다.** Mac(개발/기능검증 용도로 사용 가능,
Formal 환경으로는 rejected — `docs/decisions/phase2-formal-linux-environment.md`)에서 수행한 아주
짧은 smoke run 1건만 존재한다. 이 run의 latency/throughput/resource 수치는 Formal 또는 성능 비교에
사용하지 않는다 — harness semantics(연속 실행/경계/metric gating/accounting) 검증 전용.

## `pe-r1-warmup10s-measurement20s-smoke/`

P-E, R1(target 1 RPS), warmup=10s, measurement=20s, 2026-08-17.

**검증된 것**:
- 단일 k6 프로세스만 실행됨(warm-up→measurement 사이 drain 없음) — `measurement-k6.log` 1개만 존재.
- Phase boundary 단일 출처: k6 `setup()`이 잡은 `measurement_start_epoch_s`(k6-summary.json)와 셸이
  `MEASUREMENT_START`로 읽어들인 값이 `1786940090.974`로 **정확히 일치**(독립 계산 없음).
- Metric gating 정상: k6 내장 `iterations`=31(warm-up+measurement 전체) vs
  `measurement_iterations_started_total`=21(measurement만) — warm-up 트래픽은 실재하지만(10초×1RPS
  ≈10건) Formal client metric에서 올바르게 제외됨. `client_completed_total`=21=
  `measurement_iterations_started_total` — outcome invariant가 k6 측에서 정확히 성립.
- `timestamps.json` 유효 JSON, `postflight_fail` 빈 문자열(active=0, unexpected_error=0 확인),
  `stall_check.json` gap 없음, `dropped_iterations`=0.

**발견한 문제(이 smoke가 존재하는 이유)**: continuous lifecycle 도입 이후, warm-up에 도착했지만
아직 진행 중이던 요청이 measurement_start 이후에 완료되면서, Prometheus 쪽 outcome counter
windowed count(`[measurement_start, drain_end]`)에 섞여 들어가는 것을 발견 — 1차 smoke에서
Prometheus 측 완료 건수=28 vs k6 측 완료 건수=20(diff=8)로 크게 어긋났다.

**2026-08-17 1차 대응(이후 폐기, §14 참고)**: `gateway_async_active_requests`를 `measurement_start`
시점에 조회해 이 잔류분을 추정하고 Prometheus 쪽 값에서 빼는 "보정"을 `collect_phase2_formal_result.py`
에 추가했었다. **2026-08-18 정정**: 이 보정을 "exact"라고 부르는 것은 부적절하다고 판단해 폐기했다 —
(a) 활성 요청 수는 Prometheus scrape 샘플이라 `measurement_start` 정확한 순간의 상태가 아니고, (b)
잔류 요청이 반드시 completed로 끝난다는 보장이 없다(timeout/upstream error 등 다른 outcome 가능).
대신 **k6를 Formal cohort의 유일한 authoritative source로 고정**하고, Prometheus의 windowed outcome
count는 diagnostic 전용으로만 쓰기로 했다(`docs/decisions/phase2-formal-linux-environment.md` §14).

중요: **Primary throughput 지표(`measurement_window_completion_rate`, `throughput_population`)는 이
문제의 영향을 받지 않았다** — `[measurement_start, measurement_end]`(고정 길이) 윈도우에서는
warm-up-유입분과 measurement-tail-유출분이 상쇄되는 경향을 보였다(1차 smoke에서
`measurement_window_completion_rate=1.0`=target로 관측). 다만 §14는 이를 "매 run 수학적으로 정확히
상쇄된다"는 보장이 아니라, "충분한 warm-up 후 steady state에서는 empty-start bias가 제거되어 고정
window의 completion rate를 steady-state throughput으로 해석할 수 있다"는 정도로 표현을 조정했다 —
문제는 애초에 (그리고 지금도) Prometheus의 `[measurement_start, drain_end]` windowed outcome count
(현재 schema의 `server_diagnostic` 블록)에 국한됐다.

**이 smoke run 자체에서 여전히 유효한 검증**: 단일 k6 프로세스, drain 없는 continuous 실행, phase
boundary 단일 출처, k6 측 metric gating(`iterations`=31 vs `measurement_iterations_started_total`=21)
— 이것들은 코드 변경 없이 유지된다. 부하 자체는 재실행하지 않았다.

**2026-08-18 offline 재수집 검증**: 새 부하를 만들지 않고, 이 smoke의 raw 산출물(`k6-summary.json`,
`timestamps.json`)과 그때 그대로 살아있던 Prometheus 컨테이너(재생성 없이 계속 Up 상태였음)를 이용해
§14 수정본 `collect_phase2_formal_result.py`를 offline으로 재실행했다. 결과: `client_cohort`
(`measurement_iterations_started_total=21`, `completed=21`, `rejected=0`, `failed_mid_stream=0`,
`failed_no_event=0`, `invariant_ok=true`), `server_diagnostic`(`diagnostic_only=true`, Prometheus 실측
windowed 값 정상 생성), `measurement_window_completion_rate=1.0`(변경 없음) — 최종 schema가 실제
데이터로 정상 생성됨을 확인. 여섯 개 제거 대상 필드(`warmup_spillover_at_boundary` 등)는 결과/코드
어디에도 없음을 grep으로 확인.

## 결론

Unit 5.6 harness 검증(continuous lifecycle/boundary single source/client metric gating) PASS로 유지.
Cohort accounting 방식은 §14에서 k6-only로 재정의됐고, 이 재정의가 실제로 정상 동작함을 기존 smoke
raw 데이터에 대한 offline 재수집으로 확인했다(이 문서의 "1차 대응"은 폐기된 이력으로 보존). Linux
host 준비 단계로 진행 가능 — 근거 문서는 `docs/decisions/phase2-formal-linux-environment.md`
§12·§13·§14.
