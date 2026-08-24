# Unit 6 — Formal Benchmark 결과 (canonical, Unit 6.7 documentation freeze 반영 최종본)

Status: 완료 — 공식 결과 = Attempt 3(21 run) + Unit 6.5 R1 추가(3 run) + Unit 6.6 보정(A 7 run 재실행,
metric calibration 2 run). 총 24개 formal run + 2개 calibration-only run. Unit 6.7에서 추가 부하 테스트
없이 문서/코드 정합성만 검증하고 이 문서를 canonical로 확정(freeze)했다.
Date: 2026-08-15

목적: Unit 5 Screening에서 발견한 결과가 반복 실행(constant-arrival-rate, warm-up 2분 + measurement
5분)에서도 재현되는지 검증. 이 문서는 Unit 6 최초 보고 이후 사용자 검토에서 지적된 6가지 문제
(service time 오류, Little's Law 오용, load label 오해 소지, histogram bucket 정확도, A의
dropped_iterations, 문서 중복)를 모두 반영하고, Unit 6.7에서 dataset 정의/metric semantics/table
정합성을 코드 기준으로 재검증한 **canonical** 버전이며, 이전 버전(HEALTHY/SATURATION/
OVERLOAD/SEVERE_OVERLOAD 공통 라벨을 썼던 버전)을 대체한다.

## Official Formal Dataset

이 문서가 "공식 결과"로 취급하는 run의 전체 목록. 이 표에 없는 run(Attempt 1/2, old A rerun, calibration
run)은 어떤 공식 수치 계산에도 포함하지 않는다.

| config | 구성 | runs |
|---|---|---|
| B | Attempt 3: R2×1, R5×3, R12×3 + Unit 6.5: R1×1 | 8 |
| E | Attempt 3: R2×1, R5×3, R12×3 + Unit 6.5: R1×1 | 8 |
| A | Unit 6.6 rerun: R2×1, R5×3, R12×3 + Unit 6.5: R1×1 | 8 |
| **합계** | | **24 (formal)** |

**Calibration-only (Formal Dataset 미포함, §5 참고):**

| run | 목적 | runs |
|---|---|---|
| B-R2 (fine bucket) | histogram bucket 재보정 검증 | 1 |
| E-R5 (fine bucket) | histogram bucket 재보정 검증 | 1 |
| **합계** | | **2 (calibration-only)** |

**Discarded/history (공식 결과 계산에 절대 포함하지 않음, §10 참고):** Attempt 1(clock-domain 위험),
Attempt 2(Prometheus anonymous volume 재사용), A의 old rerun(`unit6-A-rerun-old/`, dropped_iterations
문제로 폐기).

## Metric Semantics (코드 기준 검증, Unit 6.7)

이 문서가 쓰는 5개 metric이 정확히 어떤 request population을 포함하는지, 추측이 아니라 실제 소스를 읽고
확정한 표. 코드 근거: `load-test-k6/scenarios/02-constant-arrival-rate.js`,
`gateway-mvc-executor-java8/.../chat/ChatController.java`,
`gateway-mvc-executor-java8/.../executor/InstrumentedRunnable.java`,
`gateway-mvc-executor-java8/.../metrics/GatewayMetrics.java`.

| metric | measurement start | measurement end/event | included population | excluded population | authoritative purpose |
|---|---|---|---|---|---|
| `client_ttfc_seconds` (k6 Trend, `outcome` tag) | iteration 시작(`Date.now()`, HTTP 요청 열기 전) | 첫 SSE event 수신(`client.on('event')` 최초 1회) | `firstEventAt`이 기록된 모든 outcome — **completed + failed_mid_stream** (02-constant-arrival-rate.js:107-109) | rejected(503, event 없음), failed_no_event | outcome별 TTFC 분해용. **completed-only 값이 아니다** — 이 문서 이전 버전의 "client_ttfc_seconds(completed만)"라는 서술은 부정확했으므로 정정 |
| `client_ttfc_completed_seconds` (k6 Trend, 전용, untagged) | iteration 시작 | 첫 SSE event 수신, **단 outcome==='completed'일 때만 add()** (02-constant-arrival-rate.js:110-112) | completed만 | rejected, failed_mid_stream, failed_no_event | Table B "TTFC(completed, k6 authoritative)" 컬럼의 실제 출처(`collect_formal_result.py:146`) |
| `gateway_ttfb_seconds` (Prometheus Histogram, label 없음) | `requestReceivedNanos`, executor 제출 전 servlet이 요청을 받은 시점 (ChatController.java:65) | 첫 chunk를 relay loop가 실제로 write()한 시점, **outcome 결정 전에 무조건 관측** (ChatController.java:220-222) | 첫 chunk가 relay된 모든 요청 — **completed + 이후에 mid-stream/deadline에서 실패한 요청도 포함** | 첫 chunk를 아예 못 받은 요청(reject, timeout_before_start, chunk 전 upstream 실패) | 서버 측 "TTFB" 전체 population. `client_ttfc_seconds`(tagged, all)와는 비교 가능하지만 `client_ttfc_completed_seconds`(completed-only)와는 population이 다름 |
| `executor_queue_wait_seconds{outcome}` (completed/failed 2-value label, Prometheus Histogram) | task `submit()` (`InstrumentedRunnable` 생성 시점, submitNanos) | task `run()`이 **pool worker thread에서** 시작한 시점 — **caller-run(CallerRunsPolicy) task는 애초에 관측 안 됨** (InstrumentedRunnable.java:44-49, GatewayMetrics.java:98-100 주석) | pool에서 실행된 task만, outcome이 정확히 "completed"인지 아닌지로 이진 분류 (ChatController.java:327-332) | **caller-run task 전체**(outcome 무관하게 이 metric에 전혀 안 잡힘) + `queueWaitSeconds`가 null인 경우(예: task_start 이전에 already-released) | Gateway 자체 executor queue 지연. **A의 R5/R12처럼 caller_runs가 큰 비중을 차지하는 config에서는 이 metric이 실제 유입량의 상당 부분(§8: R5 875건, R12 2975건)을 아예 반영하지 못한다** — Table B의 A-R5/A-R12 QueueWait(completed) 값을 해석할 때 이 population 축소를 반드시 함께 고려한다 |
| `client_stream_duration_seconds` (k6 Trend, `outcome` tag) | iteration 시작 | `sse.open()` 반환(연결 종료 — 성공/에러/강제 종료 불문, 02-constant-arrival-rate.js:114) | **모든 outcome** — 조건 없이 항상 add() | 없음 | 요청 전체 wall-clock. §1의 이론적 stream duration(7.8s) 검증에 사용(R1, 사실상 전부 completed인 조건) |

**정정 사항(Unit 6.7):** 이 문서 이전 버전은 §5-5와 §11-5에서 "`client_ttfc_seconds`(completed만)"라고
서술했다 — 코드 확인 결과 이는 부정확하다. `client_ttfc_seconds`는 outcome 태그가 있는 전체 population
Trend이고, completed만 담는 것은 별도 metric `client_ttfc_completed_seconds`다. Table B가 실제로 쓰는
값은 후자이므로 Table B 자체의 수치는 정확하지만, 그 근거로 든 metric 이름 서술은 아래 §5-5에서
정정한다.

## 0. 이번 개정에서 무엇이 바뀌었는가 (요약)

1. **Service time을 4.8s → 7.8s로 정정.** 잘못된 계산의 근거였던 워크로드 파라미터 오독을 바로잡음(§1).
2. Little's Law(L=λW)와 "worker 수 ÷ service time"이라는 단순 병렬 처리량 추정을 명확히 구분(§2).
3. 1/2/5/12 RPS에 걸었던 공통 이름(HEALTHY/SATURATION/OVERLOAD/SEVERE_OVERLOAD)을 제거하고 중립적
   R1/R2/R5/R12로 대체, config별 상태는 따로 서술(§3).
4. Prometheus classic histogram의 bucket 성김으로 인한 quantile 왜곡을 원인 규명하고 bucket을
   재보정, 대표 조건 재검증(§5).
5. Config A의 dropped_iterations(2.5~2.8%)를 `PRE_ALLOCATED_VUS` 상향으로 실제로 제거하고 A의
   formal run 7개를 재실행(§6).
6. 이전 버전에 있던 중복 표(구 label/신 label)를 제거하고 하나의 canonical 표만 유지.

## 1. Service Time — 실제 Mock Workload 기준 재계산

`scripts/run-formal-benchmark.sh`는 CHUNK_COUNT/CHUNK_INTERVAL_MS/FIRST_CHUNK_DELAY_MS를 override하지
않고 `load-test-k6/scenarios/02-constant-arrival-rate.js`의 **기본값**을 그대로 쓴다:

```
CHUNK_COUNT = 35          (스크립트 기본값 — Unit 5가 쓰던 scenario 01의 기본값 20과 다름)
CHUNK_INTERVAL_MS = 200
FIRST_CHUNK_DELAY_MS = 1000
```

theoretical stream duration = `firstChunkDelay + (chunkCount − 1) × chunkInterval`
= `1000 + 34 × 200 = 7800ms = 7.8s`

**이전 버전 문서가 쓴 4.8s는 scenario 01(Unit 5가 쓰던 스크립트)의 기본값 `chunkCount=20`을 착각해
대입한 계산 오류였다** — Unit 6는 scenario 02를 쓰므로 이 값은 처음부터 틀렸다.

**실측 검증** (`k6`의 `client_stream_duration_seconds`, R1 run 3개, Mock/Gateway가 전혀
경합하지 않는 조건):

| config | p50 | p95 | p99 | min | max |
|---|---|---|---|---|---|
| B | 7.908s | 7.987s | 8.022s | 7.853s | 8.054s |
| E | 7.956s | 8.011s | 8.024s | 7.858s | 8.027s |
| A | 7.916s | 7.998s | 8.029s | 7.857s | 8.042s |

이론값 7.8s와 실측 중앙값 7.9~7.96s가 거의 일치한다(차이는 네트워크/relay 오버헤드 수준). **문서
전체에서 4.8s를 7.8s로 교체했다.**

## 2. 수정된 Theoretical Capacity와 실측 Completion 비교

**병렬 worker 처리량 추정치(단순 산술, Little's Law 아님)**: 요청이 platform worker/thread 하나를
서비스 시간(7.8s) 동안 통째로 점유한다고 가정하면, 그 worker 수만큼 초당 처리 가능한 상한이 나온다.

```
B/A (core=max=10)  →  10 / 7.8s ≈ 1.282 req/s
E   (max=50)        →  50 / 7.8s ≈ 6.410 req/s
```

**실측 completion rate와 비교** (median completion_ratio × arrival rate):

| config-load | 실측 completion rate | 추정 capacity | 비율(실측/추정) |
|---|---|---|---|
| B-R12 | 12×11.1% ≈ 1.332/s | 1.282/s | 1.039 |
| E-R12 | 12×52.7% ≈ 6.324/s | 6.410/s | 0.987 |
| B-R5 | 5×26.7% ≈ 1.335/s | 1.282/s | 1.041 |

**실측값이 추정 capacity와 ±4% 이내로 일치한다** — 이 단순 병렬-worker 모델이 B와 E(capacity를 넘는
구간)의 sustained completion rate를 잘 설명한다는 근거다. **A는 이 모델로 설명하지 않는다** — CallerRuns가
활성화되면 core=10이라는 고정 worker 수 가정 자체가 깨지기 때문이다(§3, §4).

## 3. Queueing 현상에 대한 표현 수정

이전 버전이 "Little's Law로 sustained capacity를 계산했다"고 쓴 것은 부정확했다 — Little's Law(`L =
λW`)는 §2의 worker-수÷service-time 추정과는 다른 관계식이다. Little's Law는 Unit 6 착수 시점에
**Unit 5의 closed-model concurrency 임계값을 open-model 유입률로 환산**하는 데 실제로 쓰였다(원래
LOW=2rps 선정 근거, `unit6-benchmark-plan.md` §3) — 이 용도는 정확했다. §2의 capacity 추정은 이와
별개로 "worker 하나가 요청 하나를 서비스 시간만큼 점유한다"는 단순 산술이며, 두 개념을 이 문서에서
혼동하지 않는다.

또한 M/M/c 대기행렬의 정확한 `ρ/(1-ρ)` 공식을 근거로 들지 않는다. 안전하게: **utilization이 1에
가까워질수록 queueing delay가 비선형적으로 급증하는 현상이 관찰됐다**(B/A가 capacity 근처인 R2에서
이미 TTFC가 수십 배로 뛰는 것으로 확인, §4) — 정도로만 기술한다.

## 4. Load Axis — 중립화 및 configuration별 상태

RPS 값에 HEALTHY/SATURATION/OVERLOAD/SEVERE_OVERLOAD 같은 공통 이름을 붙이지 않는다 — config마다
capacity가 다르므로 같은 이름이 서로 다른 여유/포화 정도를 가리키게 되어 오해 소지가 있다. 아래처럼
중립적으로 표기한다.

**R1 = 1 RPS, R2 = 2 RPS, R5 = 5 RPS, R12 = 12 RPS**

### Configuration별 상태 (§2 capacity 추정 기준)

**B (core=max=10, capacity≈1.28rps)**
- R1(1rps < capacity): **stable** — reject 0%, TTFC p50/p95/p99 = 1.01s
- R2 이상(2rps > capacity): **executor capacity 초과** — reject 33%→73%→89%(R2→R5→R12), accepted 요청도
  queue capacity(20)를 채운 뒤 처리되어 TTFC p95가 16.8~16.9s로 거의 고정(§6)

**E (core=10, max=50, capacity≈6.41rps)**
- R1/R2(1~2rps ≪ capacity): **stable** — reject 0%, TTFC p50/p95/p99 = 1.01s, queue_size_peak=0
- R5(5rps, capacity의 78%): **high utilization이지만 stable** — reject 0%, completion_ratio 100%
  유지, 단 pool_size_peak가 41까지 늘어나며(thread 비용 상승) capacity에 근접했음을 보여줌
- R12(12rps > capacity): **max capacity 초과** — pool이 50에서 막히고 reject 47.3%로 급증

**A (core=10, 단 CallerRuns 활성화 이후 고정 10-worker 모델 미적용)**
- R1(1rps): **stable** — reject/timeout 0%, TTFC p50/p95/p99 = 1.01s, caller_runs=0(core만으로 충분)
- R2(2rps > core-only capacity): **core=10만으로는 부족해 queue(100)가 즉시 쓰이고, timeout이
  발생하기 시작**(timeout_rate 56.5%) — 이 시점 caller_runs는 33건(전체의 5.5%)으로 아직 작다
- R5/R12: **CallerRunsPolicy가 본격적으로 작동해 Tomcat thread가 추가 실행 자원처럼 참여** —
  caller_runs가 875건(R5)→2975건(R12)까지 늘며, 이 초과 실행 자원 덕분에 completion_ratio가 오히려
  68.5%→86.6%로 개선된다(§6). **이 구간부터는 "10-worker 고정 capacity" 모델이 A의 실제 처리 능력을
  설명하지 못한다** — 실질 동시 실행 능력이 core(10) + CallerRuns로 흡수되는 Tomcat 스레드 수만큼
  가변적으로 늘어나기 때문이다.

## 5. Histogram Quantile 정확도 문제 — 원인 규명 및 Metric Calibration

### 5-1. 문제

Attempt 3(원본 Unit 6 formal 24 run) 결과에서 다음과 같은 모순이 있었다:

```
B-R2:  client TTFC p95 ≈ 16.91s  vs  gateway TTFB p95 ≈ 28.90s  vs  queue wait p95 ≈ 28.89s
```

`QueueWait + Mock firstChunkDelay(1s) ≈ Gateway TTFB ≈ Client TTFC` 관계가 성립해야 하는데, TTFB/queue
wait가 TTFC보다 약 12초나 높게 나왔다.

### 5-2. 원인

`gateway_ttfb_seconds`/`executor_queue_wait_seconds`의 원래 Prometheus bucket 경계가
`..., 1, 2.5, 5, 10, 30, 60`으로 10~30초, 30~60초 구간이 매우 넓었다. Prometheus 공식 문서
([Histograms and summaries](https://prometheus.io/docs/practices/histograms/))에 명시된 대로,
classic histogram의 `histogram_quantile()`은 **해당 percentile이 속한 bucket 내부에서 균등분포를
가정해 선형보간**한다 — 이 Baseline의 실제 지연이 16~58초 구간에 몰려 있는데 그 구간을 커버하는 bucket이
`10→30` 단 하나뿐이었으므로, 실제 분포가 그 구간 초반(16~17초)에 몰려 있어도 보간 결과는 훨씬 위쪽으로
치우쳤다. **k6의 `client_ttfc_seconds` Trend는 원본 표본에서 직접 percentile을 계산**하므로(bucket
보간이 아님) 이 문제가 없다 — 이것이 애초에 collector가 client TTFC를 authoritative 값으로 채택한
이유이기도 하다(변경 없음, 유지).

### 5-3. 조치 — Bucket 재보정 (formal 24 run 재실행 없이)

`GatewayMetrics.java`의 `gateway_ttfb_seconds`/`executor_queue_wait_seconds`/
`executor_task_start_delay_seconds` bucket을 1초~60초 구간에서 8개 → 22개로 세분화했다
(`FINE_LATENCY_BUCKETS`, `docs/decisions/monitoring-baseline.md`에 근거 기록). **기존 24개 formal
run은 재실행하지 않는다** — 대신 대표 조건 2~3개만 짧은 calibration run(warm-up 30s + measurement
90s, 정식 반복이 아님을 `docs/test-results/phase1/unit6-metric-calibration/`에 별도 보존)으로
재검증했다.

### 5-4. Calibration 결과

| 조건 | Client TTFC p95 | Gateway TTFB p95 | QueueWait(completed) p95 | 비고 |
|---|---|---|---|---|
| B-R2 (calibration, fine bucket) | 16.77s | 17.76s | 17.73s | Attempt 3 대비 TTFB/QueueWait와 TTFC 간 격차가 **약 12초 → 약 1초**로 줄었다 |
| E-R5 (calibration, fine bucket) | 1.01s | 1.95s | 0.00095s | 절대값이 작아(1~2초대) 여전히 상대적 보간 오차가 남아있음(아래 §5-5) |
| A-R2 (formal 재실행분, fine bucket) | 52.82s | 59.44s | 52.78s | QueueWait와 TTFC는 거의 일치(≈1초 차), TTFB만 높음 — bucket 문제가 아니라 §5-5의 population 차이 |

**결론**: 지연이 큰(수 초~수십 초) 구간에서는 bucket 재보정이 실질적으로 효과가 있었다(B-R2 사례).
지연이 작은(1~2초) 구간에서는 여전히 상대오차가 남는다(E-R5 사례, 아래 §5-5) — 이 잔여 오차에 대한
Phase 2 이후 대응은 §5-6에 별도로 기록한다. **k6의 client-side Trend(`client_ttfc_completed_seconds`
등, Metric Semantics 표 참고)를 사용자 latency의 authoritative metric으로 계속 유지**하고, gateway
측 histogram quantile은 exact value로 해석하지 않는다는 원칙을 문서에 명시한다.

### 5-5. 추가로 밝혀진 것: TTFB와 QueueWait/TTFC는애초에 population이 다를 수 있다 (A-R2 사례)

A-R2에서 QueueWait(completed, 52.78s)/TTFC(completed, 52.82s)는 거의 일치하지만 TTFB(59.44s)만 유독
높다 — 이는 bucket 문제가 아니라 **측정 population의 정의 차이**이며, 추측이 아니라 코드로 증명된다
(Metric Semantics 표 참고):

- `gateway_ttfb_seconds`는 outcome label이 아예 없고, relay loop가 첫 chunk를 write한 시점에
  **outcome이 정해지기 전에 무조건** `observe()`된다(`ChatController.java:220-222`) — 그 요청이 나중에
  mid-stream/deadline에서 실패해도 이미 관측치에 들어간 뒤다.
- `executor_queue_wait_seconds{outcome="completed"}`와 `client_ttfc_completed_seconds`(Table B의 TTFC
  컬럼이 실제로 쓰는 metric)는 각각 fine outcome이 정확히 "completed"인 요청만(`ChatController.java:
  327-332`), outcome이 'completed'로 확정된 요청만(`02-constant-arrival-rate.js:110-112`) 기록한다.

즉 TTFB population ⊇ {completed} ∪ {나중에 실패한 요청 중 첫 chunk는 받은 것들}이고, QueueWait(completed)/
TTFC(completed) population = {completed}뿐이다 — **population 차이는 코드로 확정됐고, 추측이 아니다.**
A는 timeout_rate가 높아(R2에서 56.5%) "첫 chunk는 받았지만 결국 실패"하는 요청이 많고, 이런 요청은
core=10 병목으로 늦게 시작해 TTFB 자체가 데드라인에 가깝게 크다 — TTFB population에 이 느린 실패
요청들이 섞여 평균을 끌어올린다. **B는 reject-dominant(요청이 아예 relay()에 도달하지 못하고
튕겨나감)라 이 population 차이가 없어** TTFB와 QueueWait(completed)가 잘 맞았다(§5-4). 이 population
차이를 향후 결과 해석 시 metric 정의의 일부로 명시한다.

### 5-6. 잔여 오차에 대한 Phase 2 대응 — Histogram Schema Freeze

E-R5 calibration(§5-4)에서 절대값이 작은(1~2초) 구간의 상대오차가 남은 이유는 현재
`FINE_LATENCY_BUCKETS`(`GatewayMetrics.java`)가 0.5초와 2초 사이에 `1`, `2` 두 지점뿐이라 그 구간
내부에서 여전히 성기게 선형보간되기 때문이다. Phase 1의 이 formal run들은 재실행하지 않는다 — 대신
Phase 2 이후 implementation 비교에서 server-side histogram을 더 신뢰성 있게 쓸 수 있도록, 1초 부근을
더 세분화한 bucket schema를 `docs/decisions/monitoring-baseline.md`에 "Phase 2 이후 비교를 위한 최종
schema"로 freeze해 둔다(코드 변경 및 재실행은 Phase 2 착수 시점에 별도로 진행). client-side k6 Trend는
계속 user-facing latency의 source of truth로 유지한다.

## 6. A의 dropped_iterations — 원인 조사 및 해결

### 6-1. 문제와 원인

Attempt 3에서 A는 모든 부하 수준(R2/R5/R12)에서 k6 `dropped_iterations`가 2.5~2.8% 발생했다(B/E는
0%). `vus_max`가 그 시점 `MAX_VUS=1000`의 15~20%(141~151)에 불과했으므로 **VU 개수 상한 문제가
아니었다** — A의 요청 지연 분포가 유난히 넓어서(즉시 완료 vs 데드라인 근처) k6의
constant-arrival-rate 스케줄러가 새 VU를 그때그때 spin-up하는 속도가 못 따라간 것으로 추정하고,
`PRE_ALLOCATED_VUS`(사전 할당, spin-up 지연 없음)를 크게 늘려 직접 검증했다.

### 6-2. 검증 및 조치

`PRE_ALLOCATED_VUS=100 → 800`으로 올려 A, rate=12rps, 60초 진단 실행 결과: **720/720 iteration
전부 시작, dropped_iterations=0**을 확인했다. 이를 반영해 **A의 formal run 7개(R2×1, R5×3,
R12×3)를 `PRE_ALLOCATED_VUS=800`, 새 histogram bucket으로 전부 재실행**했다(구 결과는
`docs/test-results/phase1/unit6-A-rerun-old/`에 보존, 공식 결과로 미사용).

### 6-3. 재실행 결과 — Rate Fidelity

| config-load | target rate | achieved iterations | expected(target×300s) | dropped_iteration_rate |
|---|---|---|---|---|
| A-R2 | 2 req/s | 600 | 600 | 0.0% |
| A-R5 | 5 req/s | 1500 (1500–1501) | 1500 | 0.0% |
| A-R12 | 12 req/s | 3601 | 3600 | 0.0% |

**"모든 config에 정확히 같은 arrival rate가 적용됐다"를 근거 없이 단정하지 않기 위해, B/E도 포함한
전체 rate-fidelity 표를 §7 Table F로 남긴다** — B/E는 애초부터 dropped_iterations가 관측되지 않았고
(achieved iterations가 target×300s와 ±1 이내로 일치, `docs/test-results/phase1/unit6/*/measurement-k6.log`
원본 확인 가능), 이번 A 재실행으로 세 config 모두 dropped_iteration_rate=0%가 실측으로 확인됐다.

## 7. 공식 결과표 (canonical — 이 문서가 유일한 source of truth)

원본 전체: `docs/test-results/phase1/unit6/_aggregated.json` (24 formal run), calibration 원본:
`docs/test-results/phase1/unit6-metric-calibration/`.

### Table A — Throughput / Outcome (median, (min–max) when 3 reps)

| config-load | arrival rate | n_reps | completion_ratio | reject_rate | timeout_rate | failure_rate |
|---|---|---|---|---|---|---|
| B-R1 | 1 req/s | 1 | 100.0% | 0.0% | 0.0% | 0.0% |
| B-R2 | 2 req/s | 1 | 66.6% | 33.4% | 0.0% | 0.0% |
| B-R5 | 5 req/s | 3 | 26.7% (26.6%–26.7%) | 73.3% (73.3%–73.4%) | 0.0% | 0.0% |
| B-R12 | 12 req/s | 3 | 11.1% (11.1%–11.1%) | 88.9% (88.9%–88.9%) | 0.0% | 0.0% |
| E-R1 | 1 req/s | 1 | 100.0% | 0.0% | 0.0% | 0.0% |
| E-R2 | 2 req/s | 1 | 100.0% | 0.0% | 0.0% | 0.0% |
| E-R5 | 5 req/s | 3 | 100.0% | 0.0% | 0.0% | 0.0% |
| E-R12 | 12 req/s | 3 | 52.7% (52.6%–52.8%) | 47.3% (47.2%–47.4%) | 0.0% | 0.0% |
| A-R1 | 1 req/s | 1 | 100.0% | 0.0% | 0.0% | 0.0% |
| A-R2 | 2 req/s | 1 | 39.5% | 0.0% | 56.5% | 4.0% |
| A-R5 | 5 req/s | 3 | 68.5% (65.1%–69.9%) | 0.0% | 29.5% (28.1%–32.2%) | 2.0% (2.0%–2.7%) |
| A-R12 | 12 req/s | 3 | 86.6% (86.1%–87.3%) | 0.0% | 9.4% (8.7%–9.8%) | 4.1% (4.0%–4.1%) |

### Table B — Latency (seconds, median (min–max), server-side는 §5 population 주의사항 참고)

| config-load | TTFC(completed, k6 authoritative) p50 | p95 | p99 | Gateway TTFB p95 | QueueWait(completed) p95 |
|---|---|---|---|---|---|
| B-R1 | 1.01 | 1.01 | 1.01 | 2.42 | 0.00 |
| B-R2 | 16.58 | 16.91 | 16.97 | 28.90* | 28.89* |
| B-R5 | 16.80 (16.73–16.80) | 16.91 (16.83–16.93) | 16.94 (16.87–16.96) | 28.95* | 28.95* |
| B-R12 | 16.73 (16.72–16.84) | 16.81 (16.80–16.92) | 16.83 (16.82–16.94) | 28.95* | 28.95* |
| E-R1 | 1.01 | 1.01 | 1.01 | 2.42 | 0.00 |
| E-R2 | 1.01 | 1.01 | 1.01 | 2.42 | 0.00 |
| E-R5 | 1.00 (1.00–1.01) | 1.01 (1.01–1.01) | 1.01 (1.01–1.01) | 2.42 | 0.00 (0.00–0.00) |
| E-R12 | 1.00 (1.00–1.00) | 1.01 (1.01–1.01) | 1.01 (1.01–1.01) | 2.42 (2.42–2.42) | 0.00 |
| A-R1 | 1.01 | 1.01 | 1.01 | 2.42 | 0.00 |
| A-R2 | 24.27 | 52.82 | 53.08 | 59.44 | 52.78 |
| A-R5 | 1.01 (1.00–1.01) | 52.53 (36.42–52.66) | 53.03 (52.85–53.07) | 58.46 (58.42–58.46) | 54.26 (53.70–54.34) |
| A-R12 | 1.00 | 1.02 (1.01–8.10) | 52.94 (52.80–53.07) | 53.44 (53.43–53.51) | 54.37 (54.27–54.47) |

*B-R2/R5/R12의 Gateway TTFB/QueueWait는 원래(구 bucket) Attempt 3 값 그대로다(재실행 안 함) — §5-4의
calibration(B-R2, fine bucket 재검증: TTFB≈17.76s, QueueWait≈17.73s)이 "재보정하면 TTFC와 훨씬 가깝게
좁혀진다"는 것을 보여주는 참고 값이며, 이 표의 공식 수치는 아니다. A-R2/R5/R12는 §6 재실행분(fine
bucket 적용됨)이라 이미 재보정된 값이다.

### Table C — Executor / Thread saturation (median (min–max))

| config-load | pool_size_peak | queue_size_peak | caller_runs | platform_thread_peak |
|---|---|---|---|---|
| B-R1 | 10 | 0 | 0 | 39 |
| B-R2 | 10 | 20 | 0 | 45 |
| B-R5 | 10 | 20 | 0 | 46 (44–47) |
| B-R12 | 10 | 20 | 0 | 43 (42–46) |
| E-R1 | 10 | 0 | 0 | 40 |
| E-R2 | 16 | 0 | 0 | 52 |
| E-R5 | 41 | 0 | 0 | 92 (90–93) |
| E-R12 | 50 | 0 | 0 | 108 (106–109) |
| A-R1 | 10 | 0 | 0 | 39 |
| A-R2 | 10 | 100 | 33 | 48 |
| A-R5 | 10 | 100 | 875 (875–876) | 66 (66–81) |
| A-R12 | 10 | 100 | 2975 (2973–2977) | 124 (121–129) |

### Table D — Resource (median (min–max))

| config-load | CPU avg (cores) | CPU peak (cores) | Heap peak (MB) | RSS peak (MB) |
|---|---|---|---|---|
| B-R1 | 0.02 | 0.02 | 26.0 | 158.6 |
| B-R2 | 0.02 | 0.03 | 30.6 | 157.9 |
| B-R5 | 0.03 (0.02–0.03) | 0.04 (0.03–0.04) | 30.4 | 167.9 |
| B-R12 | 0.05 (0.04–0.05) | 0.06 (0.06–0.06) | 32.2 | 177.5 |
| E-R1 | 0.02 | 0.03 | 26.2 | 159.0 |
| E-R2 | 0.04 | 0.04 | 30.7 | 165.2 |
| E-R5 | 0.05 (0.05–0.06) | 0.07 (0.07–0.07) | 55.1 | 201.7 |
| E-R12 | 0.08 (0.07–0.08) | 0.10 (0.10–0.10) | 68.9 | 225.3 |
| A-R1 | 0.02 | 0.02 | 26.2 | 157.5 |
| A-R2 | 0.03 | 0.05 | 42.2 | 192.2 |
| A-R5 | 0.05 (0.04–0.05) | 0.07 (0.07–0.07) | 69.7 | 228.8 |
| A-R12 | 0.09 (0.09–0.09) | 0.13 (0.13–0.16) | 130.5 | 310.9 |

### Table E — Mock LLM bottleneck check (전체 24 formal run)

| config-load | mock_active_peak (median) | mock_waiting_peak (median) |
|---|---|---|
| B-R1 | 8 | 0 |
| B-R2 | 10 | 0 |
| B-R5 | 10 | 0 |
| B-R12 | 10 | 0 |
| E-R1 | 8 | 0 |
| E-R2 | 16 | 0 |
| E-R5 | 40 (39–41) | 0 |
| E-R12 | 50 | 0 |
| A-R1 | 8 | 0 |
| A-R2 | 16 | 0 |
| A-R5 | 40 | 0 |
| A-R12 | 95 (95–97) | 0 |

**24개 formal run 전부 `mock_waiting_peak = 0`** — Unit 6/6.5/6.6에서 관찰된 모든 saturation/reject/
timeout/caller-runs 현상은 전적으로 Gateway의 `ThreadPoolExecutor` 설정에서 비롯됐다.

### Table F — Arrival rate fidelity (target vs delivered, §6 참고)

| config-load | target rate | achieved iterations | expected(target×300s) | dropped_iteration_rate |
|---|---|---|---|---|
| B-R1 | 1 req/s | 301 | 300 | 0.0%† |
| B-R2 | 2 req/s | 601 | 600 | 0.0%† |
| B-R5 | 5 req/s | 1500 (1500–1501) | 1500 | 0.0%† |
| B-R12 | 12 req/s | 3600 (3600–3601) | 3600 | 0.0%† |
| E-R1 | 1 req/s | 301 | 300 | 0.0%† |
| E-R2 | 2 req/s | 600 | 600 | 0.0%† |
| E-R5 | 5 req/s | 1501 | 1500 | 0.0%† |
| E-R12 | 12 req/s | 3600 | 3600 | 0.0%† |
| A-R1 | 1 req/s | 300 | 300 | 0.0% |
| A-R2 | 2 req/s | 600 | 600 | 0.0% |
| A-R5 | 5 req/s | 1500 (1500–1501) | 1500 | 0.0% |
| A-R12 | 12 req/s | 3601 | 3600 | 0.0% |

†B/E는 Attempt 3 수집 스크립트에 `dropped_iteration_rate` 필드가 아직 없던 시점 데이터라 raw
result.json에는 값이 없지만, k6 원본 로그(`docs/test-results/phase1/unit6/{B,E}-*/measurement-k6.log`)에
`dropped_iterations` 항목 자체가 등장하지 않아(=0건) 0.0%로 표기했다 — 재실행 없이 원본 로그로 직접
확인.

## 8. CallerRuns 결과 (A, 재실행분 기준)

`caller_runs`는 R1 0건 → R2 33건 → R5 875~876건 → R12 2973~2977건으로 부하에 거의 선형 비례해
증가했다(반복 간 variation 0.1% 이내). R2에서는 caller_runs 비중이 작지만(33/600≈5.5%),
R5/R12에서는 전체 유입량의 상당 부분을 차지하며 §4에서 설명한 대로 이 구간부터 core=10 고정 모델이
깨진다.

## 9. 반복 run variation

R5/R12 3회 반복의 variation은 대체로 작다 — 단 **A-R12의 TTFC p95만 1.01s~8.10s로 크게 흔들린다**
(bimodal 분포의 percentile 경계 불안정성, p99는 52.8~53.1s로 안정적). A-R5의 TTFC p95도 이번 재실행에서
한 반복(36.42s)이 나머지 두 반복(52.5~52.7s)과 크게 달라 같은 현상으로 해석한다. 전체 raw 값은
`_aggregated.json`의 각 metric `values` 배열에 보존.

## 10. Experiment Integrity — Benchmark 결과 신뢰성 확보 과정

1차/2차 Benchmark 결과를 숨기지 않되(`docs/test-results/phase1/unit6-attempt1-discarded/`,
`unit6-attempt2-discarded/`, `unit6-A-rerun-old/`), **결과값으로는 사용하지 않는다**. 아래는 장시간
반복 Benchmark의 신뢰성을 실제로 어떻게 확보했는지 보여주는 validation 과정이다.

**Attempt 1** (21/21 완료, 3개 run invariant 위반): host timestamp와 Docker VM(Prometheus가 실제로
실행되는 clock domain)이 preflight 통과 이후에도 벌어질 수 있음을 확인 → **전체 폐기**, 모든 측정
타임스탬프를 Docker VM 자신의 시계(`vm_epoch()`)로 캡처하도록 수정.

**Attempt 2** (21/21 완료, 2개 run invariant 위반): clock-domain 수정은 유효했지만 원인이 아니었음이
드러남 — 직접 재현 실험으로 `docker compose up --force-recreate`가 **Prometheus의 익명 volume(TSDB
데이터)을 기본적으로 재사용**해 config 전환 후에도 이전 config의 값이 최대 5분간 그대로 서빙됨을
확인 → **전체 폐기**, `--renew-anon-volumes`(`-V`) 추가.

**Attempt 3** (공식 채택, B/E 24 run 중 21 run 이 문서의 source): anonymous volume renewal + Docker
VM 기준 timestamp + invariant validation → 21/21 valid.

**Unit 6.5**: 동일 파이프라인으로 R1 3 run 추가 → 3/3 valid.

**Unit 6.6**: service time/queueing 표현 정정, load label 중립화, histogram bucket 재보정
+ calibration 2 run, A의 dropped_iterations 원인 규명(k6 VU 사전할당 부족) 및 A 7 run 재실행 →
**A 재실행분 7/7 valid, dropped_iterations 0%**.

**Unit 6.7**(본 개정, 추가 run 없음): 새 부하 테스트 없이 이 문서의 dataset 정의/metric semantics/
markdown table 정합성을 코드 기준으로 검증하고 canonical 상태로 freeze — Official Formal Dataset
정의 추가, Metric Semantics 표 추가(코드 대조 결과 `client_ttfc_seconds`(completed만) 서술 오류 정정),
histogram bucket schema를 Phase 2용으로 `docs/decisions/monitoring-baseline.md`에 freeze.

## 11. 새로 발견한 문제 (누적)

1. Docker Desktop VM 시계가 run 도중에도 drift할 수 있음 — 구조적으로 고침(§10).
2. `docker compose up --force-recreate`는 익명 volume을 기본 재사용함 — `--renew-anon-volumes`로
   해결(§10).
3. closed-model capacity 임계값을 open-model sustained 처리량 한계로 오인하면 안 됨 — R1
   추가로 대응(Unit 6.5).
4. **(신규) Prometheus classic histogram의 bucket이 성기면 큰 값(수십 초)뿐 아니라 작은 값(1~2초)
   에서도 quantile 왜곡이 남는다** — 큰 값 구간은 bucket 재보정으로 크게 개선됐지만(§5-4), 작은 값
   구간은 상대오차가 남아 client-side Trend를 계속 authoritative로 쓴다.
5. **(신규) `gateway_ttfb_seconds`와 `executor_queue_wait_seconds{outcome="completed"}`/
   `client_ttfc_completed_seconds`는 timeout이 많은 config(A)에서 서로 다른 population을 측정한다** —
   TTFB는 "첫 chunk relay"만 조건(outcome 무관), 나머지 둘은 "끝까지 성공(completed)"만 포함하며, 이는
   코드로 확정됐다(§5-5, Metric Semantics 표). 이 차이를 bucket 문제로 오인하지 않는다.
6. **(신규) k6 constant-arrival-rate 스케줄러는 응답 지연 분산이 큰 대상에서 `PRE_ALLOCATED_VUS`가
   부족하면 VU 개수 상한(`MAX_VUS`)과 무관하게 iteration을 드롭할 수 있다** — 사전 할당을 충분히
   늘려 해결(§6).
7. 애플리케이션(Gateway) 레벨 버그는 여전히 없음 — 24개 formal run 전부 accounting invariant 통과,
   `unexpected_error` 관측 0건.
8. 실행 중 발견: 이 머신의 무관한 `prometheus` 컨테이너가 `restart: always` 정책으로 설정되어 있어,
   한 번 중지해도 Docker 데몬 재시작 등의 계기로 다시 살아날 수 있다 — Unit 6.6 작업 중 실제로
   재발해 다시 중지했다. 향후 정식 run 전 preflight에서 매번 확인해야 하는 이유.
9. **(신규, Unit 6.7) `client_ttfc_seconds`와 `client_ttfc_completed_seconds`는 서로 다른 metric이다** —
   이전 버전 문서가 "client_ttfc_seconds(completed만)"이라 서술한 것은 코드(`02-constant-arrival-rate.js
   :107-112`) 확인 결과 부정확했다. 전자는 outcome 태그가 있는 전체(completed+failed_mid_stream)
   population Trend이고, completed만 담는 것은 후자다. Table B는 실제로 후자를 쓰므로 표 수치 자체는
   맞았지만, 문서의 metric 이름 서술을 정정했다(Metric Semantics 표 참고) — 코드로 검증하지 않고
   metric 이름/population을 서술하면 이런 오류가 재현될 수 있다는 일반 교훈.

## 12. Phase 1 최종 결론 초안 (Unit 7에서 다듬을 draft — 이 문서 안에서 유일한 초안)

1. **R1에서는 B/E/A가 사실상 동일하다** — 세 config 모두 reject/timeout 0%, TTFC p50/p95/p99 = 1.01s로
   trade-off가 전혀 드러나지 않는다(§4, §7 Table B).
2. **Blocking worker의 고정 concurrency에서 처리량 상한은 worker 수와 service time으로 결정된다** —
   B/E capacity 추정치(§2, `10/7.8s ≈ 1.282 req/s`, `50/7.8s ≈ 6.410 req/s`)가 실측 completion rate와
   ±4% 이내로 일치한다.
3. **B**: backlog 상한(queue capacity 20)과 명시적 reject를 선택한다 — 자원 사용은 제한하지만, capacity를
   넘는 요청은 accept 자체를 포기한다(§4, §7 Table A).
4. **E**: queue 대신 platform thread를 늘린다 — 자신의 max capacity(6.41rps)까지는 latency/throughput을
   완전히 유지하지만, capacity를 넘는 순간 무너지고 그 이전까지도 thread/RSS 비용이 계속 증가한다(§4,
   §7 Table C/D).
5. **A**: executor reject를 피하지만 overload를 Tomcat caller thread로 전파한다 — 높은 부하에서
   completion_ratio는 CallerRuns 덕분에 오히려 개선되지만(§4, §8), 이는 executor가 애초에 제공하려던
   격리(isolation)를 훼손하는 대가이며, 세 config 중 thread/memory 비용이 가장 크다(§7 Table C/D).
6. **HttpURLConnection의 blocking read는 request deadline 이후에도 worker를 붙잡을 수 있다** —
   Baseline의 구조적 한계(absolute deadline 이후 platform worker가 blocking read에 약 2.24초 더
   붙잡히는 현상, Unit 5 0-3)로, 세 config 공통이며 Phase 1 최종 보고서에 포함한다.

**따라서 Phase 1의 결론은 "어떤 Executor 설정이 최고인가"가 아니라, "blocking I/O를 유지하는 한 overload의
비용은 queue wait / reject / additional platform threads / caller-thread propagation 중 다른 형태로
이동할 뿐, 사라지지 않는다"이다.**

(Unit 7에서 Phase 1 전체 — Unit 1~6.7 — 를 종합한 최종 보고서로 다듬는다.)
