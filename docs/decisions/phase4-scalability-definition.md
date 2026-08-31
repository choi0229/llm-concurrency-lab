# ADR: Phase 4 Scalability Boundary Definitions

Status: Accepted (Phase 4 Unit 1 scope — definitions/protocol freeze only, no load executed)
Date: 2026-08-25

이 문서는 Phase 4가 "죽지 않았다"만으로 sustainable이라고 판정하지 않기 위한 정의를 screening
데이터를 보기 전에 freeze한다. Freeze 후에는 결과를 본 뒤 정의를 바꾸지 않는다
(`docs/test-plan/phase4-design.md` §16 Unit roadmap 원칙과 동일).

## 1. Client-side Authoritative SLO

Phase 4 scalability SLO의 authoritative source는 **client-side k6/xk6-sse**다(server-side
timer는 diagnostic — `docs/decisions/phase4-metrics-contract.md` §Metrics Authority 참고, Phase 3
Unit 5의 동일 결정 계승, `docs/decisions/phase3-metrics-contract.md` §8-1).

Completed request 기준으로만 latency를 집계한다:

- TTFC p50/p95/p99
- total stream duration p50/p95/p99

Reject/failure는 completed latency 분포에 섞지 않는다.

## 2. Experimental SLO — freeze

Normal workload baseline(첫 chunk 1.0s, 전체 stream ≈7.8s, Phase 1~3에서 반복 실측)을 기준으로:

```
TTFC p95            <= 2.0s
stream duration p95 <= 10.0s
```

**채택 확정.** Production SLA가 아니다 — 정상 workload 대비 TTFC 약 2배, stream duration 약
+2.2초까지 허용해, queue/scheduler/backlog degradation을 architecture-neutral하게 구분하기 위한
Phase 4 실험 threshold다.

**이 값은 Unit 5/7 screening 결과를 본 뒤 절대 변경하지 않는다.**

### 2-1. SLO population — completed-only (Unit 4.6에서 harness에 구현)

위 SLO(TTFC/stream duration p95)의 population은 **completed request만**이다. Rejected/failed/
timeout-shaped request의 near-instant "duration"을 completed latency와 섞지 않는다 — 섞으면
aggregate p95가 실제보다 좋아 보이는 왜곡이 생긴다(예: N=100 중 50 completed(~8s) + 50
rejected(~0.01s)를 하나의 trend로 합치면 안 됨). 실제 구현/실측: `docs/test-plan/
phase4-closed-harness-protocol.md` §5a — `client_completed_ttfc_seconds`/
`client_completed_stream_duration_seconds`(k6, completed일 때만 기록)가 SLO-authoritative,
`client_ttfc_seconds`/`client_stream_duration_seconds`(전체 outcome)는 diagnostic. `completed=0`인
run은 latency를 `null`로 기록한다(0이나 임의값 fabrication 금지) — 그 자체로는 validity failure가
아니며, 이미 RED reliability 결과로 분류된다.

## 3. MRC — Maximum Reliable Concurrency

다음을 모두 만족하는 가장 큰 concurrent stream count:

- started population valid(client_cohort invariant — 시작=완료+거부+실패)
- unexpected failure = 0
- timeout = 0
- Gateway crash = 0
- Mock crash = 0
- OOM = 0
- **rejected = 0**
- LoadGen valid(§resource-safety-policy.md)
- Mock control valid
- environment valid
- postflight clean

**rejected=0을 MRC 조건에 포함하는 이유(freeze)**: MRC는 "submitted된 모든 concurrent stream을
Gateway가 실제로 수용/완료할 수 있는 최대값"을 의미한다. M1의 의도적 `AbortPolicy` rejection이
있는 concurrency는 이 정의상 MRC가 아니다 — M1의 executor rejection은 §4 MSC 이전에 이미 "모든
요청을 받아주지 못했다"는 의미이므로, reliability(수용 가능성) 자체가 깨진 것으로 본다. 따라서
M1에서는 **"첫 rejection이 관찰되는 N 바로 아래 지점"이 곧 MRC**가 된다(§5-1 참고, M1 boundary
분리 원칙과 일치, `phase4-design.md` §4-5).

## 4. MSC — Maximum Sustainable Concurrency

MRC 조건 **+**

```
TTFC p95            <= 2.0s
stream duration p95 <= 10.0s
```

를 만족하는 최대 concurrency.

**MSC <= MRC**가 항상 성립해야 한다(SLO 조건이 추가되므로). M1에서는 queue wait 때문에 MSC가
먼저 깨지고(AMBER), MRC는 더 높은 N까지(rejection이 시작되기 직전까지) 유지될 수 있다 — 이 둘을
같은 값이라고 가정하지 않는다.

## 5. MSAR — Maximum Sustainable Arrival Rate

Open-model(constant-arrival-rate) measurement에서 다음을 모두 만족하는 최대 arrival RPS:

- `dropped_iterations = 0`
- unexpected failure = 0
- timeout = 0
- Gateway crash = 0
- Mock/LoadGen control valid
- completion throughput >= actual arrival rate의 98%
- measurement 후반부에 active/backlog population이 지속적으로 증가하지 않음(§6)
- TTFC p95 <= 2.0s(§2 frozen SLO)
- stream duration p95 <= 10.0s(§2 frozen SLO)
- postflight clean

M1에서 rejection > 0이면 그 rate는 MSAR PASS가 아니다. M2/M3에서 reject=0이어도 backlog growth가
있으면 FAIL이다(§6) — **reject=0만으로 sustainable이라고 판정하지 않는다.**

### 5-1. Actual Arrival vs Target Arrival — 용어 구분

Open-model에서 "target rate"를 "throughput"이라고 부르지 않는다. 세 값을 반드시 구분한다:

- `target_arrival_rate` — k6 설정값
- `actual_started_rate` — 실제로 시작된 요청의 rate(`dropped_iterations`가 있으면 target과
  달라질 수 있음)
- `completion_throughput` — 실제 완료된 요청의 rate

## 6. Backlog Definition — Architecture-neutral 강제 금지

하나의 queue metric으로 세 모델을 억지로 통일하지 않는다:

- **M1**: `executor_queue_depth`(§metrics-contract.md)
- **M2**: `gateway_active_requests` 대비 expected steady-state concurrency, 또는 active trend
- **M3**: `gateway_active_requests` + connection/pending diagnostics

Primary 판단 기준은 **measurement 후반부 active in-flight population의 시간 추세**다 — 예:
후반 30% window에서 active population이 계속 우상향하는지. **정확한 slope 계산 방식은 Open
Formal Protocol(Unit 7.5)에서 freeze한다.**

이번 Unit에서는 원칙만 freeze한다: **"reject=0 ≠ sustainable."**

## 7. Green / Amber / Red

- **GREEN**: MRC PASS + SLO PASS(= MSC 조건 충족).
- **AMBER**: process/reliability는 유지되지만 TTFC/stream SLO failure, 또는 명확한
  backlog/queue growth가 존재.
- **RED**: rejection, timeout, unexpected failure, crash, OOM, control ceiling, OS/resource
  hard ceiling, environment invalid 중 하나.

**환경/control ceiling은 Model RED와 구분한다.** 예: `OS_FD_LIMIT`이 원인이면 "Model RED"라고
하지 않고 "Environment-limited result"로 분류한다(§8 taxonomy, `docs/decisions/
phase4-resource-safety-policy.md` §Environment Ceiling Classification과 연결).

목표는 host를 죽이는 것이 아니라 **last GREEN / first AMBER / first RED** 경계를 찾는 것이다.

### 7-1. 기계적 계산 가능성 — Unit 4.6 harness 구현

Closed harness(`docs/test-plan/phase4-closed-harness-protocol.md` §16)는 이 정의를 실제로
기계적으로 계산한다: `reliability_pass = (completed == actual_started) AND target_reached`,
`slo_pass`는 §2-1의 completed-only latency 기준. `GREEN = reliability_pass AND slo_pass`,
`AMBER = reliability_pass AND NOT slo_pass`, `RED = NOT reliability_pass`(단, `valid=true`인
run에서만 — environment/control invalid는 RED가 아니라 INVALID, 위 원칙과 동일). RED는
server-side outcome evidence(`gateway_requests_total{outcome=...}`, prewarm-corrected 값)로
`MODEL_REJECTION`/`MODEL_TIMEOUT`/`UNCLASSIFIED_MODEL_FAILURE` 등으로 세분화되며, 근거가
불충분하면 원인을 확정하지 않고 `UNCLASSIFIED_MODEL_FAILURE`/`UPSTREAM_ERROR_UNCONFIRMED_CAUSE`로
남긴다(§16 표 참고). `target_concurrency`(k6에 설정한 값)와 `actual_started`(실제 시작된 값)는
항상 분리해서 다룬다 — load generator가 계획된 concurrency를 만들지 못한 경우
(`target_reached=false`)는 Gateway 결과가 아니라 invalid measurement다.

## 8. Failure Taxonomy — freeze

```
MODEL_SATURATION
MODEL_REJECTION
MODEL_TIMEOUT
MODEL_OOM
MODEL_CRASH
SERVLET_WRITE_LIMIT
REACTOR_CONNECTION_LIMIT
DOWNSTREAM_LIMIT
LOADGEN_LIMIT
OS_FD_LIMIT
OS_PORT_LIMIT
HOST_MEMORY_LIMIT
ENVIRONMENT_STALL
UNKNOWN
```

`UNKNOWN`도 허용한다 — 증거 없이 root cause를 정하지 않는다. Unit 2 구현 중 명확한
architecture-specific category(예: M1의 특정 실패 모드)를 **추가**할 수 있지만, **screening
결과를 본 뒤 taxonomy를 결과에 맞춰 바꾸지 않는다.**

## 9. Closed Screening — Geometry & Protocol (procedure freeze, 최종 상한은 Unit 3 dependency)

Initial candidate(geometric doubling), **control-calibrated maximum까지만**:

```
50, 100, 200, 400, 800, 1600, 3200, 5000
```

무조건 5000까지 실행하지 않는다 — Unit 3 Direct Mock/LoadGen control calibration이 정한 안전
상한을 넘지 않는다.

진행 원칙: 50 → 100 → 200 → doubling. 첫 boundary 발견 후 last PASS vs first FAIL 사이를
refinement(예: 400 PASS / 800 FAIL → 600 → 필요하면 500/700). 목표: MSC interval을 대략 10~20%
이내로 좁히기.

**M1 특칙**: M1의 첫 "정상적인"(비정상 crash가 아닌, executor+queue full로 인한) rejection은 그
자체로 RED boundary evidence다 — 그 지점 이후로는 doubling을 계속하지 않고 즉시 refinement
모드로 전환한다(§4-5 M1 boundary 분리 원칙).

## 10. Open Screening — Geometry & Protocol (procedure freeze, 최종 상한은 Unit 3 dependency)

Initial candidate:

```
2, 5, 10, 20, 40, 80, 160, 320 req/s
```

**control-calibrated maximum까지만.** 첫 unsustainable 지점 이후 refinement.

## 11. Screening Hard Stop — 공통 (안전 조건은 resource-safety-policy.md 참고)

다음 중 하나가 발생하면 더 높은 N/rate로 자동 진행하지 않는다: Gateway/Mock/LoadGen crash, OOM,
EMFILE, EADDRNOTAVAIL, host memory critical, FD safety threshold(§resource-safety-policy.md),
unexpected error >=1%, timeout burst, postflight leak, environment stall, control headroom
violation, (Open만) 명확한 runaway backlog.

목적은 host destruction이 아니라 **boundary identification**이다.

## 12. Closed Formal — N_LOW / N_MSC / N_OVER

Screening 후 model별로 세 레벨 선정:

- **N_LOW**: MSC보다 충분히 낮은 stable point.
- **N_MSC**: highest sustainable candidate.
- **N_OVER**: 첫 명확한 SLO/reliability failure point.

Model마다 절대 N 값은 다를 수 있다. **정확한 N_LOW 선정 규칙(예: 각 model MSC 대비 상대적
utilization을 맞출지 여부)은 Unit 5.5 Formal Protocol Freeze에서, screening 결과를 보기 **전에**
정의해야 한다** — screening 결과를 보며 임의로 조정하지 않는다. 이번 Unit에서는 LOW/MSC/OVER의
**의미**만 freeze한다.

Formal: 3 models × 3 levels × 3 repeats = **27 canonical runs**.

## 13. Open Formal — R_LOW / R_MSAR / R_OVER

동일 원칙, Formal: 3 models × 3 levels × 3 repeats = **27 canonical runs**.

Closed 27 + Open 27 = **총 54 canonical runs**(`phase4-design.md`가 참조하는 상위 규모, 실행
시간 때문에 조건/반복을 결과를 본 뒤 임의로 줄이지 않는다 — 변경이 필요하면 각 Formal Protocol
Freeze **이전**에 결정한다).

## 14. Formal Confirmation Rule — freeze

N_MSC(또는 R_MSAR) candidate를 **3회 반복**한다.

- **3/3 PASS** → `CONFIRMED SUSTAINABLE`
- **2/3 이상 FAIL** → `CONFIRMED UNSTABLE`
- **Mixed(예: 2 PASS/1 FAIL)** → `AMBIGUOUS`

**Ambiguous 처리(freeze)**: 사전에 정의된 **최대 2회** 추가 confirmation run까지만 허용한다. 그
후에도 mixed면 **`INCONCLUSIVE RANGE`**로 기록한다. 원하는 결과가 나올 때까지 반복하지 않는다.

이 규칙은 Closed(MSC)와 Open(MSAR) 양쪽에 동일하게 적용한다.

## 15. Model Ranking 비교 원칙

H4-f(MSC ranking vs MSAR ranking 동일 여부), H4-g(MSC/MSAR와 CPU/RSS cost의 관계)는
`phase4-design.md` §13에서 최종 wording을 freeze했다 — 이 문서는 그 wording을 검증하는 데 필요한
정의(MSC/MSAR/GREEN/AMBER/RED)만 제공하고, 결과 해석 자체는 Unit 9(Formal Result Integrity
Review)/Unit 10(Final Report)에서 수행한다.

## 16. Control-censored Result Policy — freeze (Unit 3 승인 시 확정)

Unit 3 Direct Mock control calibration이 산출한:

```
SAFE_CLOSED_MAX = 640
SAFE_OPEN_MAX   = 256 req/s
```

는 **Gateway architecture의 scalability ceiling이 아니다.** 정확한 의미: "현재 Mock LLM + k6 +
native macOS control environment에서, Gateway scalability 결과를 control contamination 없이
주장할 수 있는 calibrated upper bound"다(`docs/test-results/phase4/unit3-control-calibration/
SUMMARY.md`).

### 16-1. Closed — control-censored 판정

Unit 5 Closed Screening에서 어떤 model이 `N=640`(SAFE_CLOSED_MAX)까지 계속 GREEN(reliability
PASS + SLO PASS + environment valid)이면, 그 model의 실제 MSC boundary는 **현재 control range
안에서 발견되지 않은 것**이다. 이 경우:

- `MSC = 640`이라고 기록하지 않는다.
- 대신 `MSC >= 640, CONTROL_CENSORED`로 기록한다.
- SAFE_CLOSED_MAX를 넘는 N으로 임의로 진행하지 않는다 — 대신 §16-3의 세 옵션을 사용자에게
  보고하고 승인받는다.

### 16-2. Open — control-censored 판정

동일 원칙: Unit 7에서 어떤 model이 `R=256 req/s`(SAFE_OPEN_MAX)까지 계속 GREEN이면
`MSAR = 256`이 아니라 `MSAR >= 256 req/s, CONTROL_CENSORED`로 기록한다. 새 control calibration
없이 256을 넘는 rate를 탐색하지 않는다.

### 16-3. Control range 확장이 필요할 때 — 세 옵션

Control-censored 결과가 나오면 다음 중 하나를 **사용자에게 보고하고 승인받은 뒤에만** 진행한다
(B/C를 사용자 승인 없이 자동 수행하지 않는다):

- **A.** `MSC >= 640`(또는 `MSAR >= 256`) lower-bound 결과로 Phase 4 scope를 제한하고 그대로
  Final Report에 반영한다.
- **B.** Load Generator/Direct Mock control capability를 개선한 뒤, Unit 3 calibration을 더 높은
  범위로 다시 수행한다.
- **C.** Phase 4 전용 scalable load generator/downstream control을 별도로 설계한다.

### 16-4. Ranking 원칙 — censored model 간 순서는 만들지 않는다

Control-censored model끼리는 정확한 MSC/MSAR ranking을 만들지 않는다. 예:

```
PT   MSC = 80
VT   MSC >= 640  (CONTROL_CENSORED)
WF   MSC >= 640  (CONTROL_CENSORED)
```

이면 `VT > PT`, `WF > PT`는 말할 수 있지만 `VT vs WF` 중 어느 쪽이 더 높은지는
**INCONCLUSIVE**로 기록한다. H4-f(§ H4 hypotheses, `phase4-design.md` §13)의 ranking 판정도
censored result가 존재하는 구간에서는 그 범위 안에서만(즉 censored 여부까지 포함해서) 판정한다 —
censored model들을 억지로 동순위 또는 임의 순서로 확정하지 않는다.

### 16-5. M3 ConnectionProvider와 control range의 결합

`WEBCLIENT_MAX_CONNECTIONS=800` / `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT=800`(§7-1-1)은
**`SAFE_CLOSED_MAX=640` 범위의 screening을 위한 설정**이다. `target N <= 640`인 동안에는 변경하지
않는다. 향후 control range를 640 이상으로 확장하는 경우(§16-3 B/C 경로), M3의 `maxConnections=800`
설정을 그대로 둔 채 그 위의 N에서 관찰된 boundary를 "WebFlux의 boundary"라고 주장하지 않는다 —
반드시 **new control range → headroom policy 재계산 → M3 maxConnections 재-freeze → 필요한
functional regression** 순서로 다시 거친다.
